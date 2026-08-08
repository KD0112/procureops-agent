---
document_id: EM-POL-SUPPLIER-001
tenant_id: tenant_engineering_machinery
document_type: supplier_governance_policy
version: 1.0.0
status: approved
owner: Supplier Management
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [procurement_operator, procurement_specialist, department_approver, compliance_approver, compliance_officer, supplier_manager, auditor]
source_kind: synthetic_demo
---

# 供应商准入与推荐制度

## 供应商状态

供应商状态由供应商主数据工具返回，常见状态为已批准、条件批准、暂停和禁用。Agent 不得根据网页描述或历史对话自行改变状态。

## 推荐前检查

- 供应商主体和收款主体一致。
- 产品范围覆盖本次品类。
- 资质和质量文件仍在有效期内。
- 无未关闭的重大质量或合规事件。
- 报价能够关联到明确的供应商与报价编号。

## 例外处理

条件批准或未批准供应商只能进入风险候选，不得自动成为最终方案。若业务确需使用，应由合规人员审批，并记录替代供应商不可用的证据和例外原因。

## 职责分离

供应商管理员维护主数据，但不能审批自己新建或恢复的供应商参与高风险采购。
