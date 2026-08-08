# Knowledge Governance

本目录只保存静态、版本化、可审批的组织知识。动态价格、库存、物流和订单状态禁止进入这里。

每个 Markdown 文档必须包含：

- `document_id`, `tenant_id`, `document_type`
- `version`, `status`, `owner`
- `effective_from`, `review_due`
- `classification`, `allowed_roles`
- `contains_dynamic_facts`, `source_kind`

`scripts/rebuild_knowledge_manifest.py` 生成 SHA-256 清单；测试会拒绝缺失元数据、重复 ID、未批准文档和动态事实文档。

