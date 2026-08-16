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
- `memory`: 用户级偏好候选、确认、纠错、删除、TTL 和敏感字段限制
- `evolution`: 反馈、Prompt 候选、离线门禁、合规审批、发布和回滚
- `tenancy`: Tenant Pack 发现、版本一致性、Schema 和适配器绑定
- `integrations`: ERP、供应商和物流的本地/HTTP 可替换适配器与独立契约沙箱

## 3. 本机原生运行策略

初期使用 SQLite、文件对象存储和 JSONL 审计，保证无需 Docker 即可运行。接口按照 PostgreSQL 和对象存储可替换方式设计。SQLite 不是最终企业部署结论，而是本地开发 Profile。

长期审批和恢复先通过数据库事件历史实现；当需要跨机器 Worker 时，再增加原生 PostgreSQL 与 Temporal Profile，不改变领域模型和 Harness 契约。

多附件任务由 Intake Bundle 聚合器统一处理：按 SKU/稳定描述键合并重复行，重编号后保留每个原始附件的证据；字段冲突进入人工补充。工作台删除只设置任务归档字段并终止未运行 Job，不删除受审计约束的业务记录。

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

## 5. 三种 Agent 架构

- `single`：默认路径。一个编排器调用受类型约束的工具，最少延迟、最容易定位责任。
- `multi`：Supervisor 路由到四个确定性专业审阅器，用于观察职责拆分是否改善可诊断性。
- `multi_llm`：四个专业审阅器真正调用 Model Gateway，但输出仅为 advisory；确定性工作流仍是唯一决策权威。

所有模式复用相同状态机、工具、RAG、记忆、审批和 PO 幂等实现，才能进行公平 A/B。

## 6. 多租户与企业系统边界

```text
tenant_engineering_machinery ─┐
                              ├─> 同一 Workflow / Harness / Agent / Eval
tenant_enterprise_it ─────────┘               |
                                              v
                                 Tenant-scoped Tool Gateway
                                   |        |         |
                                  ERP    Supplier  Logistics
                                   |        |         |
                          local SQLite / HTTP sandbox / HTTPS enterprise UAT
```

- `local` 是 CI 和默认网站 Profile，完全离线且可复现。
- `http_sandbox` 连接本机 8101 端口的独立服务，验证真实 HTTP 边界。
- `http_enterprise` 要求 HTTPS Endpoint 和服务密钥；缺少配置时创建 Runtime 失败，不静默降级。
- 模型和用户不能提供 Base URL。非回环 HTTP 被拒绝，避免 SSRF 和明文传输。
- HTTP 响应必须通过 Pydantic Schema 与租户一致性检查，再写入字段级证据。
