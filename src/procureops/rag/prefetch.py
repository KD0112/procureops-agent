"""Evidence preflight decisions shared by search, debug and chat adapters."""

from __future__ import annotations

from dataclasses import dataclass

from procureops.rag.retrieval import RetrievalHit


@dataclass(frozen=True, slots=True)
class PrefetchDecision:
    status: str
    should_call_llm: bool
    reason: str
    suggestions: tuple[str, ...]
    candidate_count: int
    selected_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "should_call_llm": self.should_call_llm,
            "reason": self.reason,
            "suggestions": list(self.suggestions),
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
        }


def decide_prefetch(
    query: str,
    hits: tuple[RetrievalHit, ...] | list[RetrievalHit],
    *,
    minimum_score: float = 0.2,
    minimum_hits: int = 1,
) -> PrefetchDecision:
    """Decide whether answer generation has enough governed evidence.

    This is deliberately deterministic. It prevents an LLM call when the
    retriever has no usable evidence and gives the UI an actionable next step.
    """

    selected = [hit for hit in hits if hit.score >= minimum_score]
    if len(selected) >= minimum_hits:
        return PrefetchDecision(
            status="sufficient",
            should_call_llm=True,
            reason="检索到达到阈值的授权证据。",
            suggestions=(),
            candidate_count=len(hits),
            selected_count=len(selected),
        )
    normalized = query.strip()
    suggestions = [
        "请补充产品型号、订单号或业务对象。" if len(normalized) < 8 else "请补充更具体的业务条件。",
        "请确认知识库中是否已上传对应的产品、政策或操作文档。",
        "如果资料不在知识库中，请上传相关文档后再查询。",
    ]
    return PrefetchDecision(
        status="insufficient",
        should_call_llm=False,
        reason="没有检索到足够高相关性的授权证据，已阻止回答生成。",
        suggestions=tuple(suggestions),
        candidate_count=len(hits),
        selected_count=len(selected),
    )
