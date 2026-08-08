# ADR-0002: Harness First

## Status

Accepted

## Decision

在实现业务 Agent 前，先建立 RunContext、Model Gateway、Tool Gateway、审批绑定、幂等、预算、审计和 FakeModel。

## Rationale

如果先编写自由 Agent，再补安全和可观测性，业务代码会直接依赖模型与工具 SDK，难以测试、回放和替换。Harness 只做最小内核，并立即用采购垂直流程验证，避免脱离业务建设平台。

