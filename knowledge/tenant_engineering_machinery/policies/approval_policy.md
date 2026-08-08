---
document_id: EM-POL-APPROVAL-001
tenant_id: tenant_engineering_machinery
document_type: approval_policy
version: 1.0.0
status: approved
owner: Procurement Governance
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [procurement_operator, procurement_specialist, department_approver, compliance_approver, compliance_officer, tenant_admin, auditor]
source_kind: synthetic_demo
---

# 采购风险与审批制度

## 风险分层

- R0：只读查询，可自动执行。
- R1：内部可逆草稿，可自动执行并审计。
- R2：外部可逆动作，需要业务确认。
- R3：具有财务、法律或供应风险的动作，需要正式审批。
- R4：付款、删除审计、绕过控制等动作在 MVP 中禁止。

## 审批绑定

审批必须绑定租户、任务、动作和规范化请求哈希。规范化请求包括行项目、数量、供应商、报价、总金额、风险和规则版本。任何关键字段变化都会使原审批失效。

## 职责分离

提交人不得审批自己提交的高风险采购。高金额与合规例外需要不同角色分别作出决定。

## 修改后批准

审批人修改供应商、金额或数量时，系统不得直接复用原审批结果；必须生成新任务版本、重新运行风险检查并创建新审批请求。
