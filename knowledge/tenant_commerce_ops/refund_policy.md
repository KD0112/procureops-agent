---
document_id: commerce-refund-policy-v1
tenant_id: tenant_commerce_ops
document_type: policy
version: 1.0.0
status: approved
owner: commerce-operations
effective_from: 2026-01-01
review_due: 2027-01-01
classification: internal
contains_dynamic_facts: false
allowed_roles:
  - procurement_operator
  - procurement_approver
  - compliance_approver
source_kind: manually_reviewed_demo_policy
---

# 电商退货与退款政策

## 可申请退货

签收后 7 天内，商品存在质量问题或与订单规格明显不符时，可以提交退货申请。需要保留订单号、商品 SKU、问题描述和图片证据。

## 不可直接承诺退款

Agent 只能解释政策和整理申请材料，不能直接执行退款、改价或关闭订单。退款执行需要人工审核，并以订单系统的当前状态为准。

## 分析口径

退货率使用订单数量作为分母，退货订单数量作为分子。实时订单状态、库存和价格必须通过业务数据库或只读工具查询，不能从本知识文档推断。
