---
document_id: EM-POL-PROCUREMENT-001
tenant_id: tenant_engineering_machinery
document_type: procurement_policy
version: 1.0.0
status: approved
owner: Procurement Governance
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [requester, procurement_operator, procurement_specialist, department_approver, compliance_approver, compliance_officer, auditor]
source_kind: synthetic_demo
---

# 工程机械配件采购基本制度

## 目标

采购建议必须同时满足需求完整、型号可核验、供应商可用、报价在有效期内、预算符合规则、风险已处理和证据可追踪。

## 采购需求最低字段

- 产品名称或用途
- 数量和计量单位
- 设备品牌与完整型号
- 期望到货日期和收货地区
- 预算与币种
- 申请人和成本中心

高价值液压件、电器件、发动机件还应尽量提供旧件号、铭牌、接口照片或发动机编号。关键字段不足时，系统必须进入补充信息状态，不得直接创建采购建议。

## 硬规则来源

审批金额、报价有效期、供应商状态、超预算处理和替代件确认由版本化规则引擎执行。本文用于解释原则，不是金额计算或授权的执行来源。

## 禁止行为

- 使用 RAG 中的示例文字替代当前价格、库存或物流查询。
- 将模型推断当作供应商承诺。
- 在审批前创建正式订单或付款。
- 用低价作为唯一推荐依据。
- 在缺少适配证据时承诺一定可以安装。
