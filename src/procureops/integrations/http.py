from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from procureops.harness.errors import PermanentToolError, TransientToolError


def validate_service_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("enterprise service URL scheme must be http or https")
    if not parsed.hostname:
        raise ValueError("enterprise service URL requires a hostname")
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise ValueError("enterprise service URLs require HTTPS except on loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("enterprise service URL must not contain credentials or query data")
    return value.rstrip("/")


class EnterpriseHTTPClient:
    """A narrow synchronous HTTP boundary used only by typed tool adapters."""

    def __init__(
        self,
        *,
        system_name: str,
        base_url: str,
        api_key: str,
        contract_version: str,
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not system_name or not contract_version:
            raise ValueError("system_name and contract_version are required")
        if not api_key:
            raise ValueError(f"API key is required for {system_name}")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be in (0, 30]")
        self.system_name = system_name
        self.base_url = validate_service_base_url(base_url)
        self.api_key = api_key
        self.contract_version = contract_version
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def request_json(
        self,
        *,
        method: str,
        path: str,
        tenant_id: str,
        query: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        approval_subject_hash: str | None = None,
        contract_version: str | None = None,
    ) -> dict[str, Any]:
        if not tenant_id:
            raise PermanentToolError("tenant_id is required for enterprise HTTP calls")
        if not path.startswith("/") or path.startswith("//"):
            raise PermanentToolError("enterprise HTTP path must be an absolute service path")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Tenant-ID": tenant_id,
            "X-ProcureOps-Contract-Version": contract_version or self.contract_version,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if approval_subject_hash:
            headers["X-ProcureOps-Approval-Subject"] = approval_subject_hash
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method=method,
                    url=path,
                    params=query,
                    json=json_body,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise TransientToolError(f"{self.system_name} request timeout") from exc
        except httpx.NetworkError as exc:
            raise TransientToolError(f"{self.system_name} network failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientToolError(
                f"{self.system_name} returned retryable status {response.status_code}"
            )
        if response.status_code >= 400:
            raise PermanentToolError(
                f"{self.system_name} returned permanent status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PermanentToolError(f"{self.system_name} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PermanentToolError(f"{self.system_name} response must be an object")
        response_tenant = payload.get("tenant_id")
        if response_tenant != tenant_id:
            raise PermanentToolError(f"{self.system_name} response tenant mismatch")
        return payload
