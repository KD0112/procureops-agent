from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from procureops.domain.models import RunContext, canonical_hash
from procureops.harness.audit import AuditEvent, AuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.errors import (
    DeadlineExceeded,
    PermanentToolError,
    TransientToolError,
)


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    purpose: str = Field(min_length=1)
    payload: dict[str, Any]
    response_schema: str = Field(min_length=1)


class ModelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    output: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)


class ModelClient(Protocol):
    provider: str
    model: str

    def generate(self, request: ModelRequest) -> ModelResponse: ...


class FakeModel:
    provider = "fake"
    model = "fake-model-v1"

    def __init__(self, scripted_outputs: dict[str, dict[str, Any]]) -> None:
        self.scripted_outputs = scripted_outputs
        self.calls: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if request.purpose not in self.scripted_outputs:
            raise PermanentToolError(f"no FakeModel output for purpose: {request.purpose}")
        return ModelResponse(
            output=self.scripted_outputs[request.purpose],
            provider=self.provider,
            model=self.model,
        )


class ModelGateway:
    def __init__(
        self,
        *,
        client: ModelClient,
        audit: AuditSink,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.audit = audit
        self.max_attempts = max_attempts

    def invoke(
        self,
        *,
        context: RunContext,
        ledger: RunBudgetLedger,
        request: ModelRequest,
    ) -> ModelResponse:
        if context.is_expired():
            raise DeadlineExceeded("run deadline exceeded before model call")
        payload_hash = canonical_hash(request.payload)
        for attempt in range(1, self.max_attempts + 1):
            if context.is_expired():
                raise DeadlineExceeded("run deadline exceeded during model retry")
            ledger.charge_model_call()
            self.audit.append(
                AuditEvent.from_context(
                    context,
                    "model.started",
                    payload_hash=payload_hash,
                    metadata={
                        "purpose": request.purpose,
                        "provider": self.client.provider,
                        "model": self.client.model,
                        "response_schema": request.response_schema,
                        "attempt": attempt,
                    },
                )
            )
            try:
                response = self.client.generate(request)
                ledger.charge_usage(
                    tokens=response.input_tokens + response.output_tokens,
                    cost_usd=response.cost_usd,
                )
            except TransientToolError as exc:
                if attempt < self.max_attempts:
                    self.audit.append(
                        AuditEvent.from_context(
                            context,
                            "model.retrying",
                            payload_hash=payload_hash,
                            metadata={
                                "attempt": attempt,
                                "error_type": type(exc).__name__,
                            },
                        )
                    )
                    continue
                self._audit_failure(context, payload_hash, attempt, exc)
                raise
            except Exception as exc:
                self._audit_failure(context, payload_hash, attempt, exc)
                raise

            self.audit.append(
                AuditEvent.from_context(
                    context,
                    "model.succeeded",
                    payload_hash=payload_hash,
                    metadata={
                        "provider": response.provider,
                        "model": response.model,
                        "tokens": response.input_tokens + response.output_tokens,
                        "cost_usd": response.cost_usd,
                        "attempt": attempt,
                    },
                )
            )
            return response
        raise AssertionError("unreachable model retry state")

    def _audit_failure(
        self,
        context: RunContext,
        payload_hash: str,
        attempt: int,
        error: Exception,
    ) -> None:
        self.audit.append(
            AuditEvent.from_context(
                context,
                "model.failed",
                payload_hash=payload_hash,
                metadata={
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                },
            )
        )
