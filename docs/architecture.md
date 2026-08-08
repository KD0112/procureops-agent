# Architecture

## 1. 设计原则

系统采用“确定性工作流 + Harness + 窄职责 Agent + 可靠工具”。LLM 不拥有权限、不保存业务真相，也不直接执行副作用。

```text
API / Console
    |
Procurement Workflow (authoritative state machine)
    |
Harness
    +-- Model Gateway -> FakeModel / provider adapter
    +-- Tool Gateway  -> typed tool adapters
    +-- Policy Gate   -> RBAC, risk and approval
    +-- Budget Ledger -> calls, tokens and cost
    +-- Audit Sink    -> append-only events
    +-- Replay Record -> versions and tool snapshots
```

## 2. 模块边界

- `domain`: 状态、Schema、审批绑定和业务不变量
- `harness`: 模型/工具网关、权限、预算、幂等、重试和审计
- `workflows`: 采购状态机；不得包含 Provider SDK 细节
- `tools`: 供应商、库存、物流、PO 等适配器
- `rag`: 静态知识摄取、检索、ACL 和字段证据
- `storage`: SQLite 本地实现与未来 PostgreSQL 实现
- `evals`: 组件、轨迹、结果和安全评测

## 3. 本机原生运行策略

初期使用 SQLite、文件对象存储和 JSONL 审计，保证无需 Docker 即可运行。接口按照 PostgreSQL 和对象存储可替换方式设计。SQLite 不是最终企业部署结论，而是本地开发 Profile。

长期审批和恢复先通过数据库事件历史实现；当需要跨机器 Worker 时，再增加原生 PostgreSQL 与 Temporal Profile，不改变领域模型和 Harness 契约。

## 4. 静态与动态事实边界

RAG 可以包含：

- 产品手册与型号核对指南
- 采购制度与供应商准入规则
- 审批、证据和记忆治理说明

RAG 不得包含：

- 当前价格和库存数量
- 当前物流时效
- 当前订单状态
- 未经验证的供应商报价

这些动态事实只能通过数据库或工具获得，并携带 `observed_at` 与 `valid_until`。

