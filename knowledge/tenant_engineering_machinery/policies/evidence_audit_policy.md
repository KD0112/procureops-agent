---
document_id: EM-POL-EVIDENCE-001
tenant_id: tenant_engineering_machinery
document_type: evidence_audit_policy
version: 1.0.0
status: approved
owner: Internal Audit
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [procurement_operator, procurement_specialist, department_approver, compliance_approver, compliance_officer, auditor]
source_kind: synthetic_demo
---

# 字段证据与审计制度

## 字段证据

型号、数量、供应商、价格、库存、交期、运费、风险和最终推荐必须能够追溯到文件位置、人工确认、规则结果或工具调用。

文件证据应保存页码、表名、单元格、图片区域或段落定位。工具证据应保存工具名、调用 ID、查询时间和有效期。

## 置信度

置信度只能表示提取或匹配的不确定性，不能替代证据。没有来源的高置信度字段仍是未验证字段。

## 审计事件

审计事件采用只追加方式，记录行为人、租户、任务、动作、时间、请求哈希、结果状态和版本。敏感原文和凭证不得写入普通 Trace。

## 回放

历史任务回放必须冻结输入哈希、规则、Prompt、模型策略、Tenant Pack 和工具快照，避免使用当前动态事实污染历史结果。
