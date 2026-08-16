# ADR 0004：第二租户与企业系统集成 Profile

- 状态：Accepted
- 日期：2026-08-08
- 决策版本：1.0.0

## 背景

系统已经能用工程机械租户和 SQLite 动态事实跑通采购闭环，但这不足以证明跨行业迁移和真实企业系统接入能力。本次需要增加企业 IT 设备租户，并为 ERP、供应商门户和物流平台建立可替换适配器。

当前没有任何真实企业的 Endpoint、测试账号、网络白名单或合同 Schema，因此不能宣称已经连接生产系统。

## 决策

1. 同一权威工作流支持 `tenant_engineering_machinery` 与 `tenant_enterprise_it`，租户差异只存在于 Tenant Pack、目录、知识、规则和工具适配器配置中。
2. 外部系统统一经过 Harness Tool Gateway。业务 Agent 不直接导入 `httpx`、Provider SDK 或数据库连接。
3. 提供三种显式 Profile：
   - `local`：SQLite 动态事实，默认离线测试路径；
   - `http_sandbox`：连接本机独立 FastAPI 沙箱，验证 HTTP、鉴权、超时、错误分类、幂等和证据来源；
   - `http_enterprise`：仅在显式配置 HTTPS Endpoint 与密钥后启用，用于未来真实 ERP/供应商/物流 UAT。
4. ERP 负责目录搜索与 PO 草稿；供应商系统负责有效报价和可用库存；物流系统负责动态运费与时效。RAG 不保存这些事实。
5. HTTP 读调用只对超时、429 和 5xx 做 Harness 分类重试。写调用不做盲重试，依靠上游审批绑定、`Idempotency-Key` 和外部系统幂等语义恢复。
6. HTTP 响应必须经过 Pydantic Schema 验证，并携带 `source_system`、`source_locator`、`observed_at` 与 `valid_until` 后才能成为业务证据。
7. 本机沙箱是“企业系统契约模拟器”，不是生产系统。面试和文档必须明确这一边界。

## 安全约束

- 非回环地址默认只允许 HTTPS；Base URL 只来自服务端配置，不能由用户或模型传入。
- 访问密钥只从环境变量读取，不进入 Tenant Pack、审计、Trace 或测试快照。
- 请求必须绑定 `X-Tenant-ID`；响应中的租户不一致时失败关闭。
- PO 草稿必须同时携带服务身份、幂等键和审批 subject hash。
- 未配置外部系统时不得悄悄回退到 HTTP；默认继续使用可复现的 `local` Profile。

## 验收

- 两个租户分别跑通从 Intake 到审批后 PO 草稿的同一闭环。
- 工程机械租户无法检索 IT 目录/知识，IT 租户也无法读取工程机械动态事实。
- HTTP MockTransport 覆盖成功、超时/5xx、4xx、租户错配、Schema 错误与幂等头。
- 本机沙箱覆盖 ERP、供应商、物流三个契约和重复 PO 写入。
- CI 保持 FakeModel/模拟工具，不依赖外网或付费模型。
