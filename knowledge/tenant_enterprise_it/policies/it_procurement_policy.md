---
document_id: IT-POL-PROCUREMENT-001
tenant_id: tenant_enterprise_it
document_type: procurement_policy
version: 1.0.0
status: approved
owner: IT Procurement Governance
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [requester, procurement_operator, procurement_specialist, department_approver, compliance_approver, auditor]
source_kind: synthetic_demo
---

# 企业 IT 设备采购基本制度

## 目标

企业 IT 设备采购必须优先匹配已批准的资产标准，确保设备规格、维保、信息安全、交付与资产登记信息可追踪。研发笔记本、服务器、网络设备、安全设备和机房设备使用同一采购主流程，但执行不同的静态核对规则。

## 需求最低字段

- 设备类别、数量、使用部门与成本中心；
- 资产标准 SKU 或最低技术规格；
- 期望到货日期和交付地点；
- 操作系统、维保年限或上门服务要求；
- 网络和安全设备的部署区域及安全评审编号。

研发笔记本应明确内存、存储和维保要求。服务器应明确 CPU、内存、磁盘、RAID、网卡、电源和机架条件。网络或安全设备未完成安全评审时只能形成采购建议，不能创建正式订单。

## 硬规则边界

审批金额、供应商准入、报价有效期和安全评审状态由版本化规则与业务工具执行。本文只解释制度，不提供当前价格、库存、物流时效或订单状态。

## 禁止行为

- 从知识库示例中推断当前价格、库存或到货承诺；
- 让用户偏好覆盖安全审查、供应商准入或审批阈值；
- 因型号名称相似而自动确认不同维保、接口或许可版本；
- 未审批就向 ERP 创建正式订单或触发付款。
