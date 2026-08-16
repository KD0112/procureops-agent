import httpx
import pytest

from procureops.harness.errors import PermanentToolError, TransientToolError
from procureops.integrations.http import EnterpriseHTTPClient, validate_service_base_url


def test_enterprise_http_client_binds_tenant_contract_and_idempotency() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({key.casefold(): value for key, value in request.headers.items()})
        return httpx.Response(200, json={"tenant_id": "tenant-enterprise", "items": []})

    client = EnterpriseHTTPClient(
        system_name="erp",
        base_url="https://erp.example.test",
        api_key="test-secret",
        contract_version="erp-v1",
        transport=httpx.MockTransport(handler),
    )

    payload = client.request_json(
        method="POST",
        path="/v1/purchase-orders/drafts",
        tenant_id="tenant-enterprise",
        json_body={"task_id": "task-1"},
        idempotency_key="po:tenant-enterprise:task-1:v1",
        approval_subject_hash="a" * 64,
    )

    assert payload["tenant_id"] == "tenant-enterprise"
    assert captured["x-tenant-id"] == "tenant-enterprise"
    assert captured["x-procureops-contract-version"] == "erp-v1"
    assert captured["idempotency-key"] == "po:tenant-enterprise:task-1:v1"
    assert captured["x-procureops-approval-subject"] == "a" * 64
    assert captured["authorization"] == "Bearer test-secret"


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_enterprise_http_client_classifies_retryable_failures(status_code: int) -> None:
    client = EnterpriseHTTPClient(
        system_name="supplier",
        base_url="https://supplier.example.test",
        api_key="test-secret",
        contract_version="supplier-v1",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, json={"error": "unavailable"})
        ),
    )

    with pytest.raises(TransientToolError, match="supplier"):
        client.request_json(method="GET", path="/v1/options", tenant_id="tenant-a")


def test_enterprise_http_client_rejects_permanent_and_cross_tenant_responses() -> None:
    bad_request_client = EnterpriseHTTPClient(
        system_name="logistics",
        base_url="https://logistics.example.test",
        api_key="test-secret",
        contract_version="logistics-v1",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(422, json={"error": "invalid product"})
        ),
    )
    with pytest.raises(PermanentToolError, match="status 422"):
        bad_request_client.request_json(
            method="POST",
            path="/v1/quotes",
            tenant_id="tenant-a",
            json_body={},
        )

    mismatch_client = EnterpriseHTTPClient(
        system_name="erp",
        base_url="https://erp.example.test",
        api_key="test-secret",
        contract_version="erp-v1",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"tenant_id": "tenant-b"})
        ),
    )
    with pytest.raises(PermanentToolError, match="tenant mismatch"):
        mismatch_client.request_json(method="GET", path="/v1/catalog", tenant_id="tenant-a")


def test_enterprise_http_client_classifies_transport_timeout() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream")

    client = EnterpriseHTTPClient(
        system_name="erp",
        base_url="https://erp.example.test",
        api_key="test-secret",
        contract_version="erp-v1",
        transport=httpx.MockTransport(timeout),
    )

    with pytest.raises(TransientToolError, match="timeout"):
        client.request_json(method="GET", path="/v1/catalog", tenant_id="tenant-a")


def test_base_url_policy_allows_https_and_loopback_only() -> None:
    assert validate_service_base_url("https://erp.example.com") == "https://erp.example.com"
    assert validate_service_base_url("http://127.0.0.1:8101") == "http://127.0.0.1:8101"
    with pytest.raises(ValueError, match="HTTPS"):
        validate_service_base_url("http://erp.internal.example")
    with pytest.raises(ValueError, match="scheme"):
        validate_service_base_url("file:///etc/passwd")
