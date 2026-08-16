# Public Release Checklist

这份清单用于把 ProcureOps Agent 从本地工程提交为可复现、可审阅的 GitHub 项目。

## 提交前

- 确认 `.env`、Token、数据库、虚拟环境和缓存没有进入 Git。
- 运行 `git diff --check`，修复空白和冲突标记。
- 用全新虚拟环境安装 `pip install -e ".[dev]"`。
- 运行 Ruff、compileall、pytest、CodeOps benchmark 和 CI repair benchmark。
- 检查报告是否明确区分 deterministic/FakeModel、live model 和人工评测结果。
- 检查 README 的命令、目录和环境变量与当前代码一致。

## GitHub Actions

默认 CI 只运行离线、确定性的检查，不依赖真实模型、Docker、Langfuse 或外部企业系统。这样可以避免密钥缺失或网络波动掩盖代码问题。基础设施冒烟和真实模型评测作为本地/手工验收。

## 面试展示

推荐用以下顺序演示：

1. 采购任务创建、RAG 证据、工具调用和审批暂停/恢复。
2. CI 日志诊断、RepoPilot 生成修复、测试门禁、Diff 和人工审批。
3. 评测报告、Langfuse trace（若配置）和当前限制。

## 指标口径

当前报告中的确定性 benchmark 指标用于验证状态机、隔离和审批边界，不等价于线上真实模型效果。真实模型效果必须使用独立 holdout 数据集并记录模型、Prompt、数据集版本和评测时间。
