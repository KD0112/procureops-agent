from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PreferenceCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_key: str
    value: Any
    confidence: float = Field(ge=0, le=1)
    source_hash: str


def detect_preference_candidates(text: str) -> tuple[PreferenceCandidate, ...]:
    """Detect a deliberately small allowlist of explicit, non-sensitive preferences."""
    normalized = re.sub(r"\s+", "", text)
    candidates: list[tuple[str, Any, float]] = []
    if re.search(r"(?:以后|今后|记住|偏好|希望|请).*?(?:送货|交付|配送|收货)", normalized):
        delivery_match = re.search(
            r"(工作日上午|工作日下午|工作日|周末|上午|下午|晚间)",
            normalized,
        )
        if delivery_match:
            candidates.append(("preferred_delivery_window", delivery_match.group(1), 0.92))
    strategy_match = re.search(r"优先(?:比较|考虑)?(总成本|交期|质量)", normalized)
    if strategy_match:
        candidates.append(("preferred_supplier_strategy", strategy_match.group(1), 0.9))
    if re.search(r"(?:默认|以后|今后).{0,8}(?:接受|允许)等效件", normalized):
        candidates.append(("allow_equivalent_preference", True, 0.9))
    if re.search(r"(?:默认|以后|今后).{0,8}(?:不接受|禁止)等效件", normalized):
        candidates = [item for item in candidates if item[0] != "allow_equivalent_preference"]
        candidates.append(("allow_equivalent_preference", False, 0.95))
    return tuple(
        PreferenceCandidate(
            memory_key=key,
            value=value,
            confidence=confidence,
            source_hash=_source_hash(text=text, key=key, value=value),
        )
        for key, value, confidence in candidates
    )


def _source_hash(*, text: str, key: str, value: Any) -> str:
    payload = json.dumps(
        {"source": text, "key": key, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
