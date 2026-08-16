# Procurement Evidence Skill

## Purpose

对采购需求执行只读的商品匹配、供应商筛选和物流报价核验，返回带证据数量和 warning 的结构化结果。

## Trigger

- 用户提出采购、询价、供应商或物流时触发。
- 缺少商品匹配时返回 `no_match`，不能猜测型号。

## Inputs

- `tenant_id`
- `query`
- `quantity`

## Tool boundary

Skill 只通过 `ToolGateway` 调用 `catalog_lookup`、`supplier_lookup`、`logistics_quote`。ToolGateway 可以选择 `local`、HTTP 或只读 MCP profile；Skill 不直接访问数据库或 MCP transport。

## Safety

- 所有调用必须携带租户上下文。
- 本 Skill 只读，不创建订单、不扣库存、不生成最终 PO。
- 价格、库存和物流数据必须带来源；高风险写操作交给原有审批状态机。
- 工具失败时返回结构化 warning，不编造结果。

## Acceptance

- 商品不存在：`no_match`。
- 商品存在但无批准供应商：`needs_input`。
- 商品、供应商、物流都有结果：`matched`。
- 跨租户请求被 ToolGateway 拒绝。
- 任何写工具不能通过本 Skill 触发。
