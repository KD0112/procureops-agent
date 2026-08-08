---
document_id: EM-POL-QUOTE-001
tenant_id: tenant_engineering_machinery
document_type: quotation_policy
version: 1.0.0
status: approved
owner: Procurement Operations
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [procurement_operator, procurement_specialist, department_approver, compliance_approver, compliance_officer, auditor]
source_kind: synthetic_demo
---

# 报价、库存和物流事实管理制度

## 权威来源

报价、库存数量、预计出库日期、物流费用和物流时效必须来自受控工具或数据库快照。RAG 只能解释查询与比较规则。

## 快照字段

每条动态事实至少保存：供应商、产品、数值、币种或单位、查询工具、查询时间、有效截止时间和工具调用 ID。

## 过期与冲突

- 超过规则引擎有效期的报价不得用于最终推荐。
- 多个工具结果冲突时，保留全部快照并暂停自动决策。
- 缺少币种、税费口径或有效期的报价只能作为未验证候选。
- 工具调用失败时不得从历史聊天或知识文档补造当前值。

## 异常低价

异常低价是风险信号，不是自动优势。系统应检查品质档位、是否含税、运费、质保、数量阶梯和产品适配，并按规则触发人工复核。
