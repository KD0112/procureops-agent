# Demo Guide

## 1. 初始化与验证

```powershell
cd "D:\new things\项目1\project2"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

验收输出应包含知识文档校验、Ruff 通过、测试通过和覆盖率不低于 90%。普通验证不调用真实 API。

## 2. 两分钟 Happy Path

```powershell
& ".\.venv\Scripts\python.exe" scripts\demo_happy_path.py
```

讲解顺序：采购文本 → SQLite 混合 RAG 引用 → 目录匹配 → 数据库报价/库存 → Decimal 总成本 → `awaiting_approval` → 参数哈希审批 → `completed` → 唯一 PO 草稿。

重复运行会创建新任务；同一任务在代码测试中重复恢复不会创建第二份 PO。

## 3. 端到端与 A/B

```powershell
& ".\.venv\Scripts\python.exe" scripts\generate_eval_dataset.py
& ".\.venv\Scripts\python.exe" scripts\run_evaluation.py
```

报告位于 `var/evals/<run-id>/`，包括单 Agent、确定性多 Agent、FakeModel 多 Agent、300 个回放包以及两份 A/B 报告。`var/` 已被 Git 忽略。

## 4. 网站演示

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_api.py
# 浏览器访问 http://127.0.0.1:8000
```

演示顺序：

1. 新建采购任务，先选“单 Agent + 工具”，运行 Worker，展示证据、审批和 PO 草稿。
2. 输入“以后送货请安排在工作日上午”，运行后打开“用户记忆”，确认候选；新任务会出现 `memory.preferred_delivery_window` 证据。
3. 打开“进化治理”，提交纠错反馈，从反馈创建候选，依次执行离线评测、合规审批、人工发布，并演示回滚。
4. 再以“确定性多 Agent”创建任务，在时间线查看 `supervisor.trace`。
5. 只有模型配置完整时才选择“模型多 Agent”；该模式每个专业 Agent 都经过 Model Gateway。

## 5. 真实模型接入

项目只读加载 `day1/.env`。文本和视觉模型统一经过 Model Gateway；请求载荷只记录哈希，API Key 不进入审计事件。

真实模型主要用于无法由确定性解析器处理的自然语言和图片。金额、审批、权限、目录真值、价格和库存不会交给模型决定。

千问的文本和视觉模型共用 DashScope OpenAI-compatible 适配：

```powershell
$env:AGENT_TEXT_PROVIDER="qwen"
$env:AGENT_VISION_PROVIDER="qwen"
$env:DASHSCOPE_API_KEY="<your-key>"
$env:QWEN_TEXT_MODEL="qwen-flash"
$env:QWEN_VISION_MODEL="qwen-vl-plus"
$env:PROCUREOPS_ENABLE_LIVE_MODELS="1"
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py --provider qwen --limit 10
& ".\.venv\Scripts\python.exe" scripts\run_live_vision_smoke.py --provider qwen
```
