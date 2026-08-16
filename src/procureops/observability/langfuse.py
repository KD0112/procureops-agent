from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from procureops.domain.models import RunContext
from procureops.harness.audit import AuditEvent, AuditSink


def _enabled(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class LangfuseSettings:
    enabled: bool = False
    public_key: str = ""
    secret_key: str = ""
    base_url: str = "https://cloud.langfuse.com"
    environment: str = "local"
    release: str = "procureops-local"
    capture_io: bool = False

    @classmethod
    def from_environment(cls) -> LangfuseSettings:
        return cls(
            enabled=_enabled(os.getenv("LANGFUSE_ENABLED")),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
            base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").strip(),
            environment=os.getenv("LANGFUSE_ENVIRONMENT", "local").strip() or "local",
            release=os.getenv("LANGFUSE_RELEASE", "procureops-local").strip()
            or "procureops-local",
            capture_io=_enabled(os.getenv("LANGFUSE_CAPTURE_IO")),
        )

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.public_key and self.secret_key)


class _NoopObservation:
    def update(self, **_kwargs: Any) -> None:
        return None

    def score(self, **_kwargs: Any) -> None:
        return None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_value(value: Any, *, capture_io: bool, key: str = "") -> Any:
    """Keep telemetry useful while making raw business data opt-in."""
    sensitive = any(
        marker in key.casefold()
        for marker in ("key", "token", "secret", "password", "credential", "authorization")
    )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if sensitive:
            return "[REDACTED]"
        if not capture_io:
            return {"type": "redacted", "chars": len(value), "sha256": _sha256(value)}
        return value[:2000]
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe_value(item_value, capture_io=capture_io, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item, capture_io=capture_io, key=key) for item in list(value)[:50]]
    return _safe_value(str(value), capture_io=capture_io, key=key)


class _ObservationHandle:
    def __init__(self, tracer: LangfuseTracer, observation: Any) -> None:
        self._tracer = tracer
        self._observation = observation

    def update(self, **kwargs: Any) -> None:
        safe = {
            key: _safe_value(value, capture_io=self._tracer.settings.capture_io, key=key)
            for key, value in kwargs.items()
        }
        try:
            self._observation.update(**safe)
        except Exception:
            return None

    def score(
        self,
        *,
        name: str,
        value: float,
        comment: str | None = None,
        data_type: str = "NUMERIC",
    ) -> None:
        self._tracer.record_score(
            observation=self._observation,
            name=name,
            value=value,
            comment=comment,
            data_type=data_type,
        )


class LangfuseTracer:
    """Small failure-isolating Langfuse facade used by API and workers."""

    def __init__(self, settings: LangfuseSettings) -> None:
        self.settings = settings
        self._client_instance: Any | None = None
        self._import_checked = False
        self._import_available = False

    @classmethod
    def from_environment(cls) -> LangfuseTracer:
        return cls(LangfuseSettings.from_environment())

    @property
    def available(self) -> bool:
        if not self.settings.configured:
            return False
        if not self._import_checked:
            self._import_checked = True
            try:
                import langfuse  # noqa: F401
            except ImportError:
                self._import_available = False
            else:
                self._import_available = True
        return self._import_available

    def _client(self) -> Any | None:
        if not self.available:
            return None
        if self._client_instance is None:
            try:
                from langfuse import get_client

                if self.settings.base_url:
                    os.environ.setdefault("LANGFUSE_BASE_URL", self.settings.base_url)
                self._client_instance = get_client()
            except Exception:
                return None
        return self._client_instance

    @contextmanager
    def observe(
        self,
        *,
        name: str,
        as_type: str = "span",
        context: RunContext | None = None,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
    ) -> Iterator[_ObservationHandle | _NoopObservation]:
        client = self._client()
        if client is None:
            yield _NoopObservation()
            return
        attributes: dict[str, Any] = {
            "environment": self.settings.environment,
            "version": self.settings.release,
        }
        if context is not None:
            attributes.update(
                {
                    "user_id": context.actor_id,
                    "session_id": context.correlation_id,
                    "trace_name": f"procureops:{name}",
                }
            )
        try:
            from langfuse import propagate_attributes

            propagation = propagate_attributes(**attributes)
        except Exception:
            propagation = contextlib.nullcontext()
        try:
            observation_context = client.start_as_current_observation(
                as_type=as_type,
                name=name,
                input=_safe_value(input, capture_io=self.settings.capture_io),
                model=model,
            )
        except Exception:
            # Telemetry must never make a business request fail during setup.
            yield _NoopObservation()
            return
        try:
            with observation_context as observation:
                safe_metadata = _safe_value(
                    dict(metadata or {}), capture_io=self.settings.capture_io
                )
                with contextlib.suppress(Exception):
                    observation.update(metadata=safe_metadata)
                with propagation:
                    try:
                        yield _ObservationHandle(self, observation)
                    except Exception as exc:
                        with contextlib.suppress(Exception):
                            observation.update(
                                level="ERROR", status_message=type(exc).__name__
                            )
                        raise
        finally:
            self.flush()

    def record_score(
        self,
        *,
        observation: Any,
        name: str,
        value: float,
        comment: str | None,
        data_type: str,
    ) -> None:
        client = self._client()
        if client is None:
            return
        trace_id = getattr(observation, "trace_id", None)
        if not trace_id:
            return
        try:
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=float(value),
                comment=comment,
                data_type=data_type,
            )
        except Exception:
            return None

    def audit_sink(self) -> AuditSink:
        return LangfuseAuditSink(self)

    def flush(self) -> None:
        client = self._client_instance
        if client is None:
            return
        try:
            client.flush()
        except Exception:
            return None


class LangfuseAuditSink:
    """Map immutable audit events to low-cardinality Langfuse spans."""

    def __init__(self, tracer: LangfuseTracer) -> None:
        self.tracer = tracer

    def append(self, event: AuditEvent) -> None:
        with self.tracer.observe(
            name=f"audit:{event.event_type}",
            as_type="span",
            input={"event_id": event.event_id},
            metadata={
                "run_id": event.run_id,
                "task_id": event.task_id,
                "tenant_id": event.tenant_id,
                "correlation_id": event.correlation_id,
            },
        ) as observation:
            observation.update(
                output={
                    "event_type": event.event_type,
                    "payload_hash": event.payload_hash,
                }
            )
