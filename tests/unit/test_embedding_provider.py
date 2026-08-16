from __future__ import annotations

from typing import Any

import pytest

from procureops.rag.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    embedding_provider_from_environment,
)
from procureops.rag.index import HashingEmbeddingProvider


class FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.payload: dict[str, Any] | None = None

    def post(self, *, url, headers, payload, timeout_seconds):
        del url, headers, timeout_seconds
        self.payload = payload
        return self.response


def test_openai_compatible_embedding_provider_validates_and_normalizes() -> None:
    transport = FakeTransport(
        {"data": [{"index": 0, "embedding": [3.0, 4.0, 0.0]}]}
    )
    provider = OpenAICompatibleEmbeddingProvider(
        provider="test",
        model="embed-v1",
        dimensions=3,
        base_url="https://embedding.example.test/v1",
        api_key="secret",
        transport=transport,
    )

    vectors = provider.embed(["pump"])

    assert vectors == [[0.6, 0.8, 0.0]]
    assert transport.payload == {"model": "embed-v1", "input": ["pump"]}


def test_openai_compatible_embedding_provider_rejects_wrong_dimensions() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        provider="test",
        model="embed-v1",
        dimensions=3,
        base_url="https://embedding.example.test/v1",
        api_key="secret",
        transport=FakeTransport({"data": [{"index": 0, "embedding": [1.0, 2.0]}]}),
    )

    with pytest.raises(ValueError, match="dimensions"):
        provider.embed(["pump"])


def test_embedding_provider_environment_profiles(monkeypatch) -> None:
    monkeypatch.setenv("PROCUREOPS_EMBEDDING_PROFILE", "hashing")
    monkeypatch.setenv("PROCUREOPS_EMBEDDING_DIMENSIONS", "64")
    provider = embedding_provider_from_environment()
    assert isinstance(provider, HashingEmbeddingProvider)
    assert provider.dimensions == 64

    monkeypatch.setenv("PROCUREOPS_EMBEDDING_PROFILE", "unsupported")
    with pytest.raises(ValueError, match="unsupported"):
        embedding_provider_from_environment()

    monkeypatch.setenv("PROCUREOPS_EMBEDDING_PROFILE", "openai_compatible")
    for name in (
        "PROCUREOPS_EMBEDDING_PROVIDER",
        "PROCUREOPS_EMBEDDING_MODEL",
        "PROCUREOPS_EMBEDDING_BASE_URL",
        "PROCUREOPS_EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="incomplete"):
        embedding_provider_from_environment()

    monkeypatch.setenv("PROCUREOPS_EMBEDDING_PROVIDER", "test")
    monkeypatch.setenv("PROCUREOPS_EMBEDDING_MODEL", "embed-v1")
    monkeypatch.setenv("PROCUREOPS_EMBEDDING_BASE_URL", "https://embed.example.test/v1")
    monkeypatch.setenv("PROCUREOPS_EMBEDDING_API_KEY", "test-secret")
    configured = embedding_provider_from_environment()
    assert isinstance(configured, OpenAICompatibleEmbeddingProvider)
    assert configured.dimensions == 64


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"base_url": "file:///tmp/model"}, "HTTP"),
        ({"base_url": "http://embed.example.test"}, "HTTPS"),
        ({"api_key": ""}, "required"),
        ({"dimensions": 0}, "positive"),
        ({"timeout_seconds": 0}, "timeout"),
    ],
)
def test_embedding_provider_rejects_unsafe_configuration(updates, message) -> None:
    values = {
        "provider": "test",
        "model": "embed-v1",
        "dimensions": 3,
        "base_url": "https://embed.example.test/v1",
        "api_key": "secret",
    }
    values.update(updates)
    with pytest.raises(ValueError, match=message):
        OpenAICompatibleEmbeddingProvider(**values)


def test_embedding_provider_handles_empty_and_wrong_vector_count() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        provider="test",
        model="embed-v1",
        dimensions=3,
        base_url="https://embedding.example.test/v1",
        api_key="secret",
        transport=FakeTransport({"data": []}),
    )
    assert provider.embed([]) == []
    with pytest.raises(ValueError, match="vector count"):
        provider.embed(["pump"])
