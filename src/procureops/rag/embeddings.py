from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse

from procureops.harness.provider_clients import JsonTransport, UrllibJsonTransport


@dataclass(slots=True)
class OpenAICompatibleEmbeddingProvider:
    provider: str
    model: str
    dimensions: int
    base_url: str
    api_key: str
    transport: JsonTransport = field(default_factory=UrllibJsonTransport)
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("embedding base URL must be HTTP(S) with a hostname")
        if parsed.scheme != "https" and not loopback:
            raise ValueError("embedding base URL requires HTTPS except on loopback")
        if not self.provider or not self.model or not self.api_key:
            raise ValueError("embedding provider, model, and API key are required")
        if self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("embedding timeout must be in (0, 60]")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.transport.post(
            url=f"{self.base_url.rstrip('/')}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload={"model": self.model, "input": list(texts)},
            timeout_seconds=self.timeout_seconds,
        )
        raw_data = response.get("data")
        if not isinstance(raw_data, list) or len(raw_data) != len(texts):
            raise ValueError("embedding response returned the wrong vector count")
        ordered = sorted(raw_data, key=lambda item: int(item.get("index", -1)))
        vectors: list[list[float]] = []
        for item in ordered:
            raw_vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(raw_vector, list) or len(raw_vector) != self.dimensions:
                raise ValueError("embedding response dimensions do not match configuration")
            vector = [float(value) for value in raw_vector]
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector] if norm else vector)
        return vectors


def embedding_provider_from_environment():
    from procureops.rag.index import HashingEmbeddingProvider

    profile = os.environ.get("PROCUREOPS_EMBEDDING_PROFILE", "hashing").casefold()
    dimensions = int(os.environ.get("PROCUREOPS_EMBEDDING_DIMENSIONS", "256"))
    if profile == "hashing":
        return HashingEmbeddingProvider(dimensions=dimensions)
    if profile != "openai_compatible":
        raise ValueError("unsupported embedding profile")
    required = {
        "provider": os.environ.get("PROCUREOPS_EMBEDDING_PROVIDER", ""),
        "model": os.environ.get("PROCUREOPS_EMBEDDING_MODEL", ""),
        "base_url": os.environ.get("PROCUREOPS_EMBEDDING_BASE_URL", ""),
        "api_key": os.environ.get("PROCUREOPS_EMBEDDING_API_KEY", ""),
    }
    if not all(required.values()):
        raise ValueError("openai-compatible embedding configuration is incomplete")
    return OpenAICompatibleEmbeddingProvider(
        **required,
        dimensions=dimensions,
        timeout_seconds=float(
            os.environ.get("PROCUREOPS_EMBEDDING_TIMEOUT_SECONDS", "30")
        ),
    )
