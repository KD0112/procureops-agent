"""Generate real DeepEval inputs from the local CommerceOps API and an LLM.

The gold labels are intentionally kept in a separate manually reviewed JSONL
file. This script only creates actual model outputs and retrieval contexts; it
never turns workflow status into an answer-quality score.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procureops.api import create_app  # noqa: E402
from procureops.config import load_environment  # noqa: E402

INPUT_LABELS = PROJECT_ROOT / "data" / "evals" / "commerce_ops_human_labeled_v1.jsonl"
OUTPUT_PATH = PROJECT_ROOT / "reports" / "deepeval_input_commerce_ops.jsonl"
HEADERS = {
    "X-Tenant-Id": "tenant_commerce_ops",
    "X-Actor-Id": "deepeval-data-builder",
    "X-Actor-Roles": "procurement_operator",
}


def rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_prompt(query: str, payload: dict[str, object]) -> str:
    analytics = json.dumps(payload.get("analytics", {}), ensure_ascii=False, indent=2)
    evidence = json.dumps(payload.get("policy_evidence", []), ensure_ascii=False, indent=2)
    prefetch = json.dumps(payload.get("prefetch", {}), ensure_ascii=False, indent=2)
    return f"""你是 CommerceOps 企业分析助手。请只根据给定证据回答用户问题。

用户问题：{query}

只读 SQL 分析结果：
{analytics}

授权 RAG 政策证据：
{evidence}

Prefetch 证据门禁：
{prefetch}

要求：
1. 动态订单统计只能引用 SQL 分析结果；政策结论只能引用授权 RAG 证据。
2. 证据不足时明确说“当前证据不足”，并给出补充资料建议，不要猜测。
3. 简洁回答，并说明关键数字、口径或证据边界。
"""


def main() -> None:
    load_environment(PROJECT_ROOT)
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "").strip()
    if not api_key or not base_url or not model:
        raise SystemExit(
            "DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL and DEEPSEEK_MODEL are required "
            "for real output generation"
        )
    from openai import OpenAI

    labels = rows(INPUT_LABELS)
    if not labels:
        raise SystemExit("manual label file is empty")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=2)
    outputs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="procureops-deepeval-") as directory:
        root = Path(directory)
        app = create_app(
            project_root=PROJECT_ROOT,
            database_path=root / "api.sqlite3",
            var_root=root / "var",
            allow_header_auth=True,
        )
        try:
            with TestClient(app) as api:
                for row in labels:
                    query = str(row["input"])
                    response = api.post(
                        "/api/commerce/insights",
                        headers=HEADERS,
                        json={"query": query, "limit": 10},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    completion = client.chat.completions.create(
                        model=model,
                        temperature=0,
                        messages=[
                            {
                                "role": "system",
                                "content": "你必须严格遵守证据边界，不得编造政策或订单事实。",
                            },
                            {"role": "user", "content": build_prompt(query, payload)},
                        ],
                    )
                    actual_output = completion.choices[0].message.content or ""
                    context = [
                        "SQL analytics: "
                        + json.dumps(payload["analytics"], ensure_ascii=False),
                        *[
                            "RAG evidence: "
                            + json.dumps(item, ensure_ascii=False)
                            for item in payload.get("policy_evidence", [])
                        ],
                    ]
                    if not payload.get("policy_evidence"):
                        context.append(
                            "RAG evidence: no sufficiently relevant authorized policy chunk"
                        )
                    outputs.append(
                        {
                            "case_id": row["case_id"],
                            "input": query,
                            "actual_output": actual_output,
                            "expected_output": row["expected_output"],
                            "retrieval_context": context,
                            "metadata": {
                                "judge_input_source": "local CommerceOps API",
                                "dataset_version": "commerce-demo-v1",
                                "label_status": row.get("label_status"),
                            },
                        }
                    )
        finally:
            app.state.runtime.commerce.close()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in outputs) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(outputs)} real DeepEval inputs to {OUTPUT_PATH}")
    print(f"provider=deepseek model={model}")


if __name__ == "__main__":
    main()
