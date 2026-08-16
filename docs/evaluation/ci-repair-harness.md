# RepoPilot CI 代码问题诊断与修复 Harness

更新时间：2026-08-16

## 目标

把编码助手从“收到文件修改请求”补成一个更接近企业研发流程的闭环：

```text
CI 日志 -> 只读诊断 -> 结构化修复计划 -> disposable workspace
       -> 测试门禁 -> Diff + SHA-256 -> 人工审批停点
```

## API

入口：`POST /api/skills/repo-ci-repair`

它复用 RepoPilot 的受限请求契约，新增 `ci_output` 字段：

```json
{
  "description": "修复 CI 中失败的单元测试",
  "ci_output": "FAILED tests/test_hello.py::test_value - AssertionError",
  "files_to_read": ["hello.py"],
  "proposed_writes": {
    "hello.py": "def value():\n    return 2\n"
  },
  "test_command": "python -m pytest -q",
  "commit_requested": true
}
```

当前接口故意不让 Agent 根据日志直接生成并执行任意命令。`proposed_writes` 应由经过上游约束的 planner 或人工提供；写入只发生在临时工作区。

返回结果新增：

- `diagnosis`：失败类别、失败测试、脱敏证据、修复提示和可行动性；
- `workflow`：实际经过的阶段；
- `diff_sha256`：当前候选 Diff 的哈希；
- `status`：`passed`、`failed`、`blocked` 或 `needs_approval`。

## 工具边界

- `repo_diagnose_ci` 是 R0 只读工具，只解析 bounded CI 文本，不执行日志中的内容；
- `repo_write_file` 仍是 R1，只能写 disposable workspace；
- `repo_run_tests` 仍只允许单一 pytest、ruff 或 compileall；
- 测试不通过时，`commit_requested=true` 也不会进入审批，返回 `failed`；
- 测试通过后，提交请求停在 `needs_approval`，当前不会自动 commit/push；
- 原始仓库始终不变，结果中的 `diff_sha256` 用于人工审批时绑定候选补丁。

## 离线评测

运行：

```powershell
cd "D:\new things\项目1\project2"
& ".\.venv\Scripts\python.exe" scripts\run_ci_repair_benchmark.py
```

报告：

- `reports/latest_ci_repair_benchmark.json`
- `reports/latest_ci_repair_benchmark.md`

指标：

- `diagnosis_accuracy`：常见 CI 类型分类准确率；
- `repair_test_gate`：候选修复是否通过测试门禁；
- `approval_boundary`：通过测试的外部副作用是否停在人工审批；
- `source_isolation`：源仓库是否保持不变。

这些指标验证 Harness 安全与流程，不等于 SWE-bench 或自然语言代码修复能力。

## 面试表达

> 我没有让 Coding Agent 直接执行 CI 日志或任意 Shell。系统先用只读诊断器把失败日志归类，再把结构化补丁放进隔离工作区，执行 allowlist 测试，生成带 SHA-256 的 Diff，最后在人工审批点停止。测试失败不能进入审批，源仓库也不会被 Agent 直接修改。
