# Code-Agent Harness Benchmark

- Dataset: `data/evals/code_agent_v1.jsonl` (30 cases)
- Run at: `2026-08-16T04:10:43.306077+00:00`
- Type: deterministic offline Harness checks; not an LLM quality score.

| Metric | Value |
|---|---:|
| status_accuracy | 1.0 |
| source_isolation_rate | 1.0 |
| blocked_or_approval_precision | 1.0 |

| Category | Cases | Status pass rate |
|---|---:|---:|
| workspace_isolation | 5 | 1.000 |
| path_traversal | 5 | 1.000 |
| sensitive_path | 5 | 1.000 |
| command_injection | 5 | 1.000 |
| approval_boundary | 5 | 1.000 |
| test_gate | 5 | 1.000 |

## Interpretation

The benchmark verifies the safety contract around a coding-agent Harness: source-tree isolation, path policy, command policy, test gating and approval stop points. It does not claim SWE-bench or natural-language code quality performance.
