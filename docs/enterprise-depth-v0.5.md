# ProcureOps v0.5：第二租户与企业系统集成验收

## 结论

v0.5 完成企业 IT 设备第二租户，并把 ERP、供应商和物流从“仓库内函数”提升为可替换的企业 HTTP 契约。默认 CI 仍完全离线；本机可启动独立沙箱演示真实网络边界；未来获得企业 UAT 信息后可切换 `http_enterprise`，不需要重写 Agent 或工作流。

## 第二租户证明了什么

- 同一 `SingleAgentWorkflow`、Supervisor、Harness、状态机、审批与 PO 幂等代码处理两个行业；
- IT 租户有独立产品 Schema、8 条目录种子、3 个供应商、报价库存、物流、审批规则和 RAG 文档；
- 网站身份选择器可以切换租户，服务端从成员表解析角色；
- 20 条增量 E2E 全通过，包括工程机械租户无法匹配 IT SKU 的反向隔离用例；
- RAG 检索按 `tenant_id + role + classification` 过滤，IT 任务只产生 `IT-*` 引用。

## 企业系统契约

| 系统 | 读/写职责 | 关键治理 |
|---|---|---|
| ERP | 目录搜索、PO 草稿 | HTTPS/回环限制、合同版本、审批哈希、幂等键 |
| 供应商系统 | 有效报价与可用库存 | 动态时间、有效期、租户绑定、Schema 校验 |
| 物流系统 | 运费与时效 | 动态时间、有效期、临时/永久错误分类 |

Tool Gateway 仍是唯一调用入口。LLM 不能获取 HTTP Client，不能提供 URL，也不能绕过审批。

## 三档 Profile

- `local`：SQLite，适合 CI、回放和普通网站演示；
- `http_sandbox`：本机独立服务，适合面试演示 HTTP 与故障治理；
- `http_enterprise`：真实 UAT 入口；缺少 HTTPS Endpoint 或密钥时失败关闭。

## 必须主动说明的边界

当前没有连接某家公司的生产 ERP、供应商网络或物流平台，因为没有目标企业提供的 Endpoint、凭据、网络白名单和字段合同。项目实现的是可落地的适配器、治理和独立沙箱，而不是虚构“生产已接通”。这恰好是面试中的风险意识加分点。

## 最新验收证据

- `scripts/verify.ps1`：Ruff、109 条 Pytest、90.29% 覆盖率、SQLite integrity/foreign key/index 检查全部通过；
- 主集三路 A/B：100/100、100/100、100/100，安全率均为 100%，仍推荐默认单 Agent；
- 跨租户 IT 集：20/20，安全率 100%；
- 真实本机 HTTP 冒烟：IT 任务通过 ERP、供应商、物流网络适配器完成，PO 草稿包含 ERP 外部回执，证据数 15。
