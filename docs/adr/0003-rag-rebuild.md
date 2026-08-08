# ADR-0003: Rebuild RAG Sources and Index

## Status

Accepted

## Context

旧 `day1` RAG 已具备 Markdown 元数据、版本和索引指纹，静态产品分类与型号核对内容有复用价值。但它面向客服，使用 Chroma，并在长文档中混入市场参考价格、物流时效、客服话术和销售建议。

## Decision

- 不复制旧 Chroma 数据库。
- 不直接加载旧 `rag_chat.py` 或 `knowledge_tool.py`。
- 只迁移静态产品分类、同义词、型号确认字段和安全边界。
- 采购政策、供应商治理、审批、证据和记忆制度重新生成。
- 所有新文档具有租户、版本、Owner、分类、状态、有效期和动态事实标记。

## Consequences

需要重新构建 Embedding 索引，但能够保证新项目的租户 ACL、证据链、文档审批和静态/动态事实边界。

