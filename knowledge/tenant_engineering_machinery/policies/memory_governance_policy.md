---
document_id: EM-POL-MEMORY-001
tenant_id: tenant_engineering_machinery
document_type: memory_governance_policy
version: 1.0.0
status: approved
owner: Data Governance
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [requester, procurement_operator, procurement_specialist, tenant_admin, auditor]
source_kind: synthetic_demo
---

# 用户偏好记忆治理制度

## 可记忆内容

经用户确认的品牌偏好、交付偏好、品质档位和常用规格可以保存。密码、API Key、身份证件、银行卡、健康信息和无关私人信息禁止写入。

## 生命周期

模型只能生成 `PENDING` 候选。用户确认后变为 `ACTIVE`；纠错产生新版本；撤销或删除后立即停止使用；到期后变为 `EXPIRED`。

## 使用边界

记忆只影响候选排序和交互体验，不能覆盖预算、供应商禁用、审批和合规硬规则。系统必须向用户展示本次使用了哪些记忆。

## 隔离

个人记忆绑定租户和用户。组织级记忆必须经过管理员批准，不能由单个用户对话自动升级。
