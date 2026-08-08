# ProcureOps Agent

ProcureOps Agent 是一个以采购任务为中心的企业 Agent 项目。第一阶段先实现可审计、可回放、默认拒绝的 Harness，再接入单 Agent 采购闭环，最后通过对照评测决定是否保留多 Agent 架构。

## 当前状态

- 已建立独立项目，不修改 `day1` 或旧 `day1/project2`。
- 已确定旧 RAG 只复用静态领域知识，不复用旧 Chroma 索引。
- 已实现 Harness v1：运行上下文、模型/工具网关、分类重试、审批绑定、双层幂等、预算和追加式审计。
- 已实现 SQLite 迁移、任务状态机、目录/供应商/报价/库存工具、Decimal 成本和 PO 草稿。
- 已实现文本、Excel、PDF 和图片 Intake；模型提取通过可替换网关接入，CI 使用 FakeModel。
- 已实现受治理 SQLite 混合 RAG 索引、字段证据、确认式用户记忆、审批暂停/恢复和任务回放。
- 已建立 100 条端到端数据集，并运行单 Agent 与 Supervisor+专业 Agent A/B。
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

第一条命令完全不调用 LLM，会展示任务暂停审批和恢复后生成 PO 草稿。评测命令运行相同的 100 条用例并输出单/多 Agent 对照报告。

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

## Local task workbench (v0.2)

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_api.py
# Open http://127.0.0.1:8000
```

The UI, API and worker share the same SQLite repository, state machine, durable queue and Harness. Use the workbench button to process one job at a time, or run a continuous worker in another terminal:

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_worker.py --loop
```

Paid/live models remain opt-in. The normal verification path is fully offline:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py --limit 10
& ".\.venv\Scripts\python.exe" scripts\run_live_vision_smoke.py
```

Interview assets are under `demo_assets/`; the eight-minute walkthrough is `docs/interview/demo-script.md`.
