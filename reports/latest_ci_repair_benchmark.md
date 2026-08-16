# CI Repair Harness Benchmark

- Dataset: `5` deterministic diagnosis cases + 1 repair flow
- Run at: `2026-08-16T05:06:44.948817+00:00`
- Type: offline, deterministic Harness benchmark; not an LLM code-generation score.

| Metric | Value |
|---|---:|
| diagnosis_accuracy | 1.0 |
| repair_test_gate | 1.0 |
| approval_boundary | 1.0 |
| source_isolation | 1.0 |

## Verified workflow

`CI output -> read-only diagnosis -> structured patch -> isolated workspace -> test gate -> diff hash -> human approval stop`

The source repository remains unchanged. A `needs_approval` result means the candidate diff and passing test result are ready for a human decision; this profile does not auto-commit or push.
