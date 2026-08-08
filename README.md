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
- 第二租户暂缓，但 Tenant Pack 接口保留。

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
- `docs/governed-evolution-and-models.md`

## Local task workbench (v0.4)

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_api.py
# Open http://127.0.0.1:8000
```

The UI, API and worker share the same SQLite repository, state machine, durable queue and Harness. Use the workbench button to process one job at a time, or run a continuous worker in another terminal:

首次打开会要求使用本机演示账号登录。默认密码为 `ProcureOps-Demo-2026!`，可通过 `PROCUREOPS_DEMO_PASSWORD` 覆盖：

- `buyer@procureops.local`：创建采购任务；
- `approver@procureops.local`：审批普通/部门采购；
- `compliance@procureops.local`：合规审批与 Prompt 发布。

采购任务必须由另一个账号审批，借此演示 maker-checker 职责分离。

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
v0.4 的五项企业深化证据见 `docs/enterprise-depth-v0.4.md`。
