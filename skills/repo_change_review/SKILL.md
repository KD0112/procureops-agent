# Repo Change Review Skill

## Purpose

把“编码助手”限制为一个可审计的仓库变更审查流程：读取上下文、生成候选补丁、在隔离工作区执行测试、返回 diff，并在需要时停在人工审批前。Skill 不直接修改主仓库，也不负责自动 push。

## Trigger

- 用户要求修复、重构或补测试，并明确给出允许的仓库范围。
- API 入口：`POST /api/skills/repo-change-review`。
- 真实生产接入时，调用方必须先完成身份、租户和仓库授权。

## Inputs

- `description`：任务目标和验收标准。
- `requested_files` / `files_to_read`：最小阅读范围。
- `proposed_writes`：由 planner 生成的候选文件内容。
- `expected_sha256`：已知文件的版本哈希；缺省时 Skill 会先读取当前版本并绑定到本次写入。
- `ci_output`：可选的 CI 日志片段；只进入只读诊断器，不会被当作 Shell 命令执行。
- `test_command`：只允许单一的 pytest、ruff 或 compileall 命令。
- `commit_requested`：只会产生 `needs_approval`，不会自动提交。

## Tool boundary

所有操作都经 `ToolGateway`：

- R0：`repo_tree`、`repo_read`、`repo_search`、`repo_diff`。
- R0：`repo_diagnose_ci`，解析常见 pytest/ruff/SyntaxError/依赖/超时日志并输出诊断，不修改文件。
- R1：`repo_write_file`、`repo_run_tests`，只作用于 disposable workspace。
- R2：`repo_commit`，当前 fail-closed，必须接入精确绑定的 approval 和 git adapter 后才能实现。

## Safety and acceptance

- 路径必须是 workspace 内的相对路径；`.git`、`.env`、密钥、缓存和运行产物被拒绝。
- 已存在文件必须进行版本哈希校验，避免并发任务覆盖新版本。
- 工作区以源仓库副本创建，源仓库在整个流程中保持不变。
- 命令使用 `shell=False`，拒绝 shell chaining、环境变量展开和任意解释器代码。
- 测试输出只保留尾部有限大小，并禁用自动加载的第三方 pytest 插件，避免测试过程执行未授权代码或遥测。
- 测试失败返回 `failed`；策略、路径和工具边界错误返回 `blocked`；提交请求返回 `needs_approval`。
- 结果必须包含 workspace id、变更文件、diff、测试返回码和审计事件数量。
- CI 修复入口 `POST /api/skills/repo-ci-repair` 的标准顺序是：诊断日志、验证结构化计划、隔离工作区写入、测试门禁、Diff 与 SHA-256、人工审批停点。

## Non-goals

当前版本不是 Claude Code/OpenClaw 的完整替代品：不包含自主 push、联网浏览器操作、任意 shell、后台常驻 daemon 或真实 SWE-bench 成绩。扩展这些能力前必须增加沙箱、审批、数据集和回放证据。
