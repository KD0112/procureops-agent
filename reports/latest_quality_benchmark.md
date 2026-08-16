# Agent quality benchmark (measured)

> This file is generated from a local deterministic run. It contains measured values, not a claim about production traffic.

- Dataset size: `200`
- Dataset versions: `3.0.0`
- Splits: `{'development': 120, 'holdout': 20, 'regression': 60}`

## Architecture metrics

| Architecture | Success | Safety | Evidence | P50 ms | P95 ms | Avg tools | Model calls | Cost USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single | 1.000 | 1.000 | 1.000 | 377.8 | 531.4 | 3.38 | 0 | 0.0000 |
| multi | 1.000 | 1.000 | 1.000 | 375.8 | 528.7 | 3.38 | 0 | 0.0000 |
| multi_llm | 1.000 | 1.000 | 1.000 | 378.8 | 520.8 | 3.38 | 1012 | 0.0000 |

## Baseline versus multi-agent

| Metric | Baseline single | Multi | Delta | Relative delta |
|---|---:|---:|---:|---:|
| dataset_size | 200 | 200 | 0 | 0.0 |
| task_success_rate | 1.0 | 1.0 | 0.0 | 0.0 |
| safety_pass_rate | 1.0 | 1.0 | 0.0 | 0.0 |
| evidence_coverage | 1.0 | 1.0 | 0.0 | 0.0 |
| latency_p50_ms | 377.82 | 375.816 | -2.004 | -0.005304 |
| latency_p95_ms | 531.41 | 528.671 | -2.739 | -0.005154 |
| average_tool_calls | 3.38 | 3.38 | 0.0 | 0.0 |
| total_model_calls | 0 | 0 | 0 | 0.0 |
| estimated_total_cost_usd | 0.0 | 0.0 | 0.0 | 0.0 |

## Interpretation

- Use `task_success_rate`, `safety_pass_rate`, and `evidence_coverage` as quality gates.
- Treat P95 latency, average tool calls, and cost as regression constraints.
- Run DeepEval separately with real `actual_output` and retrieval context; the harness status is not an answer-quality substitute.
