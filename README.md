# ProcureOps Agent

ProcureOps Agent 是一个以采购任务为中心的企业 Agent 项目。第一阶段先实现可审计、可回放、默认拒绝的 Harness，再接入单 Agent 采购闭环，最后通过对照评测决定是否保留多 Agent 架构。

## 当前状态

- 已建立独立项目，不修改 `day1` 或旧 `day1/project2`。
- 已确定旧 RAG 只复用静态领域知识，不复用旧 Chroma 索引。
- 已实现 Harness v1：运行上下文、模型/工具网关、分类重试、审批绑定、双层幂等、预算和追加式审计。
- 已实现 SQLite 迁移、任务状态机、目录/供应商/报价/库存工具、Decimal 成本和 PO 草稿。
- 已实现文本、Excel、PDF 和图片 Intake；模型提取通过可替换网关接入，CI 使用 FakeModel。
- 已实现受治理 SQLite 混合 RAG 索引、字段证据、候选/确认式用户记忆、完整性校验、投毒防护、访问审计、审批暂停/恢复和任务回放。
- 已实现反馈 → Prompt 候选 → 20 条 Gold Set 基线/候选回归门禁 → 合规审批 → 人工发布 → 回滚的受治理进化闭环。
- 已建立 100 条端到端数据集，并运行单 Agent、确定性多 Agent、FakeModel 多 Agent 三路对照。
- 已接入 DeepSeek、智谱与千问的 OpenAI-compatible Harness 适配；Qwen 配置完成后自动成为首选，并支持降级和熔断。千问真实调用仍需 DashScope 密钥。
- 已实现动态物流工具、确定性用户偏好决策与只读白名单内运行的受限 Supplier Research Agent。
- 已实现本地服务端身份、租户成员角色、maker-checker 和事务 Outbox；正常模式不信任客户端角色请求头。
- 当前 A/B 证据支持默认采用单 Agent；多 Agent 组件保留为实验路径。
- 已实现企业 IT 设备第二租户，并用同一状态机、Harness、Agent、RAG、记忆与审批闭环完成跨行业验证。
- 已实现 ERP、供应商、物流的 `local / http_sandbox / http_enterprise` 三档适配器；本机沙箱不冒充真实生产系统。
- 已实现单任务最多 5 个附件的证据合并与冲突 fail-closed，并提供保留审计的任务软删除。
- 已实现持久化 SSE 任务事件流、只读 MCP transport、BM25 + Vector + RRF、Evidence Judge 与 development/regression/locked holdout 评测分层。
- 已新增 CI/代码问题诊断闭环：`/api/skills/repo-ci-repair` 只读解析 CI 日志，再在隔离工作区执行结构化修复、测试门禁、Diff/SHA-256 和人工审批停点。
- 当前网站默认 `single` 且 `PROCUREOPS_ENABLE_LIVE_MODELS=0`，普通演示不调用 DeepSeek、GLM 或 Qwen；模型多 Agent 必须显式开启。

## 核心不变量

1. Agent 不能直接访问业务数据库或外部 HTTP API。
2. 动态价格、库存、物流和订单状态不能来自 RAG。
3. 高风险动作没有有效审批时必须失败。
4. 审批绑定不可变参数哈希；参数变化后旧审批失效。
5. 同一幂等键最多产生一个业务副作用。
6. 每次模型调用、工具调用和决策都必须携带 `RunContext`。
7. 自动测试默认使用 FakeModel 和模拟工具，不需要付费 API。
8. 用户偏好只能影响允许的供应商排序字段，不能修改审批、策略或权限。
9. 任务创建与 Worker 意图必须经同一数据库事务写入，投递失败可幂等恢复。

## 本地开发

```powershell
cd "D:\new things\项目1\project2"
py -3.12 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

`.env` 只保存旧 `day1/.env` 的路径，不复制任何 API Key。新项目加载旧环境变量时不会修改原文件。

## 本地演示

```powershell
& ".\.venv\Scripts\python.exe" scripts\demo_happy_path.py
& ".\.venv\Scripts\python.exe" scripts\generate_eval_dataset.py
& ".\.venv\Scripts\python.exe" scripts\run_evaluation.py
& ".\.venv\Scripts\python.exe" scripts\run_cross_tenant_evaluation.py
```

第一条命令完全不调用 LLM，会展示任务暂停审批和恢复后生成 PO 草稿。评测命令运行相同的 100 条用例并输出三种 Agent 架构对照报告；模型多 Agent 使用 FakeModel，不调用付费 API。

## 文档入口

- `docs/PRD.md`
- `docs/architecture.md`
- `docs/harness-contract.md`
- `docs/threat-model.md`
- `docs/evaluation-plan.md`
- `docs/interview/question-map.md`
- `docs/development-sequence.md`
- `docs/implementation-status.md`
- `docs/demo-guide.md`
- `docs/evaluation-results.md`
- `docs/evaluation/agent-evaluation-observability.md`
- `docs/evaluation/coding-agent-harness.md`
- `docs/evaluation/ci-repair-harness.md`
- `skills/repo_change_review/SKILL.md`
- `docs/governed-evolution-and-models.md`
- `docs/enterprise-depth-v0.5.md`
- `docs/phase-1-5-acceptance.md`
- `docs/public-release-checklist.md`
- `docs/core-concepts-and-demo.md`：SSE、MCP、RAG、Evidence Judge、Holdout、多 Agent 和当前模型状态的白话讲解
- `docs/interview/00_项目总览与验收矩阵.md`
- `docs/interview/01_面试演示与简历写法.md`
- `docs/interview/02_高频追问与参考回答.md`

## Local task workbench (v0.5)

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_api.py
# Open http://127.0.0.1:8030
```

The UI, API and worker share the same SQLite repository, state machine, durable queue and Harness. Use the workbench button to process one job at a time, or run a continuous worker in another terminal:

首次打开会自动使用本机“采购申请人”身份，不需要密码。需要审批或发布 Prompt 时，点击右上角“切换身份”：

- `buyer@procureops.local`：创建采购任务；
- `approver@procureops.local`：审批普通/部门采购；
- `compliance@procureops.local`：合规审批与 Prompt 发布。

采购任务必须由另一个本机身份审批，借此演示 maker-checker 职责分离。这个免密码身份选择器只面向单机演示；部署到企业环境时应替换为公司 SSO/OIDC。

身份窗口现在也可以切换“工程机械配件”和“企业 IT 设备”两个租户。切换租户会创建独立 Bearer Session，任务、记忆、RAG、动态事实、审批和审计均按租户隔离。

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_worker.py --loop
```

Paid/live models remain opt-in. The normal verification path is fully offline:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py --limit 10
& ".\.venv\Scripts\python.exe" scripts\run_live_vision_smoke.py
# 千问配置好 DASHSCOPE_API_KEY 后：
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py --provider qwen --limit 20
& ".\.venv\Scripts\python.exe" scripts\run_live_vision_smoke.py --provider qwen
```

Interview assets are under `demo_assets/`; the eight-minute walkthrough is `docs/interview/demo-script.md`.

## Enterprise infrastructure profile

The default offline profile remains SQLite + `SQLiteWorkQueue`. The optional enterprise profile adds FastAPI `/api/chat`, `/api/search`, `/api/documents`, Redis Session/Tool Cache/Rate Limit, MySQL async business persistence, Redis Streams document ingestion, and the `procurement_evidence` Skill. Details and acceptance evidence are in `docs/enterprise-infrastructure.md`.

```powershell
docker compose -f docker-compose.infra.yml up -d
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev,infra]"
& ".\.venv\Scripts\python.exe" -m pip install numpy faiss-cpu
$env:PROCUREOPS_MYSQL_URL="mysql+asyncmy://procureops:procureops-local@127.0.0.1:3307/procureops"
$env:PROCUREOPS_REDIS_URL="redis://127.0.0.1:6380/0"
$env:PROCUREOPS_QUEUE_BACKEND="redis-streams"
& ".\.venv\Scripts\python.exe" scripts\init_mysql.py
& ".\.venv\Scripts\python.exe" scripts\run_api.py
```

To use the Redis Streams RAG worker, the environment above already sets `PROCUREOPS_QUEUE_BACKEND=redis-streams`; run `scripts\run_rag_stream_worker.py --loop` in another terminal. User uploads are staged by default; only a compliance approver can request indexing into the governed RAG corpus.

For a one-command infrastructure acceptance after Docker Desktop is running, set the three environment variables above and run:

```powershell
& ".\.venv\Scripts\python.exe" scripts\smoke_infra.py
```

The script deliberately fails if it falls back to in-memory cache or SQLite, so its `enterprise infrastructure smoke: PASS` output is the evidence to keep for the interview.
v0.4 的五项企业深化证据见 `docs/enterprise-depth-v0.4.md`；第二租户与企业系统集成见 `docs/enterprise-depth-v0.5.md`。

## 本机 ERP / 供应商 / 物流 HTTP 演示

终端一启动独立契约沙箱：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_integration_sandbox.py
```

终端二显式选择 HTTP 沙箱，再启动网站：

```powershell
$env:PROCUREOPS_INTEGRATION_PROFILE="http_sandbox"
$env:PROCUREOPS_ERP_BASE_URL="http://127.0.0.1:8101"
$env:PROCUREOPS_SUPPLIER_BASE_URL="http://127.0.0.1:8101"
$env:PROCUREOPS_LOGISTICS_BASE_URL="http://127.0.0.1:8101"
$env:PROCUREOPS_INTEGRATION_API_KEY="local-only-not-a-secret"
& ".\.venv\Scripts\python.exe" scripts\run_api.py
```

也可以在终端二先运行一次无页面冒烟闭环：

```powershell
& ".\.venv\Scripts\python.exe" scripts\demo_external_systems.py
```

或使用单命令临时启动并自动关闭沙箱：

```powershell
& ".\.venv\Scripts\python.exe" scripts\smoke_external_http.py
```

访问 `http://127.0.0.1:8030`，选择企业 IT 租户后创建 `IT-LAP-DEV-14 | 研发笔记本 | 2 | 台`。证据链会显示外部系统工具来源，审批后的 PO 草稿包含 ERP 外部回执。真实企业 UAT 使用 `http_enterprise`，并要求 HTTPS Endpoint 与私密服务凭据。
