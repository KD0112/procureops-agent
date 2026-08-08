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

报告位于 `var/evals/<run-id>/`，包括单 Agent、多 Agent、200 个回放包和 `ab_comparison.json`。`var/` 已被 Git 忽略。

## 4. 真实模型接入

项目只读加载 `day1/.env`。文本和视觉模型统一经过 Model Gateway；请求载荷只记录哈希，API Key 不进入审计事件。

真实模型主要用于无法由确定性解析器处理的自然语言和图片。金额、审批、权限、目录真值、价格和库存不会交给模型决定。
