# Demo Guide

## 1. 初始化与验证

```powershell
cd "D:\new things\项目1\project2"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

验收输出应包含知识文档校验、Ruff 通过、测试通过和覆盖率不低于 90%。普通验证不调用真实 API。
脚本还会执行 SQLite `integrity_check`、外键检查、七个迁移版本校验以及物流、记忆、会话、Outbox 四条查询的 `EXPLAIN QUERY PLAN`。

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

1. 打开页面后系统自动进入“采购申请人”身份；新建“单 Agent + 工具”任务并运行 Worker。
2. 待审批后点击“切换身份”，选择“部门审批人”批准，再运行 Worker，展示 maker-checker、证据和 PO 草稿。整个本机演示不需要密码。
3. 输入“以后供应商优先比较交期”，运行后打开“用户记忆”，确认候选；下一任务会以交期策略选择供应商并记录物流证据。
4. 打开“进化治理”，提交纠错反馈，从反馈创建候选，运行 20 条 Gold Set 基线/候选回归；切换合规账号审批、发布并演示回滚。
5. 再以“确定性多 Agent”创建任务，在时间线查看 `supervisor.trace`。
6. 只有模型配置完整时才选择“模型多 Agent”；Supplier Research Agent 最多 3 步且只能访问只读物流工具。

## 5. 真实模型接入

项目只读加载 `day1/.env`。文本和视觉模型统一经过 Model Gateway；请求载荷只记录哈希，API Key 不进入审计事件。

真实模型主要用于无法由确定性解析器处理的自然语言和图片。金额、审批、权限、目录真值、价格和库存不会交给模型决定。

千问的文本和视觉模型共用 DashScope OpenAI-compatible 适配：

```powershell
$env:AGENT_TEXT_ROUTE="qwen,deepseek"
$env:AGENT_VISION_ROUTE="qwen,zhipu"
$env:DASHSCOPE_API_KEY="<your-key>"
$env:QWEN_TEXT_MODEL="qwen-flash"
$env:QWEN_VISION_MODEL="qwen-vl-plus"
$env:PROCUREOPS_ENABLE_LIVE_MODELS="1"
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py --provider qwen --limit 10
& ".\.venv\Scripts\python.exe" scripts\run_live_vision_smoke.py --provider qwen
```
