# Harness Contract v0.1

本文是所有 Agent、工作流和工具必须遵守的强制契约。

## HARN-001 Run Context

每次执行必须携带不可变 `RunContext`：

- `run_id`, `task_id`, `tenant_id`, `actor_id`, `actor_roles`
- `workflow_version`, `prompt_version`, `model_policy_version`
- `rule_set_version`, `tenant_pack_version`
- `deadline_at`, 模型/工具/Token/费用预算

租户或行为人缺失时默认拒绝。

## HARN-002 Model Gateway

业务模块不得直接导入 Provider SDK。模型调用必须经过 Model Gateway，并记录请求类型、模型版本、结构化响应、Token、耗时和失败分类。日志不得保存 API Key。

## HARN-003 Tool Gateway

工具必须注册风险等级、所需角色、是否写操作、幂等策略和重试策略。Agent 不能直接访问数据库连接或 HTTP 客户端。

## HARN-004 Fail Closed

权限、租户、证据、审批或 Schema 信息不完整时停止执行，不允许模型猜测后继续。

## HARN-005 Retry Classification

仅网络超时、限流和明确的临时服务错误可重试。权限拒绝、业务校验、审批拒绝和参数错误不得重试。

## HARN-006 Idempotency

所有写工具必须使用幂等键。成功结果写入后，相同幂等键只能返回原结果。相同键配合不同请求哈希必须报冲突。

## HARN-007 Approval Binding

审批绑定 `tenant_id + task_id + action + subject_hash`。价格、数量、供应商或目标变化会改变哈希并使审批失效。

## HARN-008 Evidence

关键字段必须记录来源类型、来源 ID、定位符、时间、有效期、置信度和产生者。无证据字段不得进入自动采购建议。

## HARN-009 Audit

审计事件只追加。敏感载荷仅记录哈希与允许的摘要。纠错通过追加新事件表达。

## HARN-010 Replay

每次运行冻结输入哈希、Prompt、模型策略、规则、Tenant Pack、工具快照与最终状态。CI 默认使用 FakeModel 和 Mock Tool。

## HARN-011 Memory Governance

模型只能提出记忆候选。未确认记忆不得激活；用户可以纠错、撤销、删除；敏感字段默认禁止；记忆不能覆盖硬规则。

## HARN-012 Release Gate

Prompt、模型、规则或工具发生变化时，必须运行离线评测。安全不变量失败时禁止发布，不允许用平均分掩盖高风险失败。

