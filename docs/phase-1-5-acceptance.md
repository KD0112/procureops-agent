# Project2 阶段 1-5 实施与验收

如果不熟悉 SSE、MCP、RRF、Evidence Judge、Holdout 或多 Agent，请先阅读 `docs/core-concepts-and-demo.md`。该文档使用业务类比解释每项能力，并明确当前网站是否调用真实模型、RAG 文档来源和推荐演示顺序。

## 参考项目取舍

| 参考项目 | 本次采用 | 明确未采用 |
|---|---|---|
| LangChain 知识库助手 | Retriever/Embedding 分层、可替换真实 Embedding、版本化检索评测 | 无证据时使用通用知识回答 |
| LangGraph + MCP 出行助手 | MCP `initialize`、`tools/list`、`tools/call`、服务端工具映射、多个服务器配置模型 | 固定 thread id、`InMemorySaver` 生产化、`eval()`、用 LangGraph 替换现有状态机 |
| Vibe Coding 指南 | 项目规则、测试先行、自动质量门禁、实现与文档同步 | 只追求快速生成而跳过租户、审批和证据约束 |
| DeepResearch / CloudAgent 企业 RAG | SSE 阶段事件、Evidence Judge、有界补充检索、BM25 + Vector + RRF、来源与哈希 | 暴露思维链、无限反思、盲目多 Agent、未经评测的性能宣传 |

## 阶段 1：SSE 任务事件流

- 端点：`GET /api/tasks/{task_id}/events/stream`
- 事件源：SQLite `workflow_events.sequence`，不是内存中的第二份状态。
- 支持：`Last-Event-ID`、15 秒 heartbeat、终态自动结束、Bearer 鉴权、租户隔离。
- 安全：递归屏蔽 prompt、instruction、reasoning、rationale、token、credential 等字段。
- 前端：使用带 Authorization header 的 `fetch` stream；任务切换时取消旧连接。

验收：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\integration\test_api.py -k event_stream -q
```

预期：`1 passed`。浏览器验收时创建任务并运行 Worker，任务时间线应自动更新，不再依赖 4 秒整页轮询。

## 阶段 2：只读 MCP

- 调用链保持为 `Agent -> ToolGateway -> Integration Suite -> MCP transport`。
- MCP 仅暴露 `catalog_lookup`、`supplier_lookup`、`logistics_quote`。
- `purchase_order_draft` 通过 MCP 会 fail closed。
- stdio transport 不启用 shell，命令、server、tool binding 均来自服务端配置。
- 每次调用执行 MCP 初始化、工具发现和工具调用，并校验协议版本、工具 allowlist、租户和 Pydantic 响应合同。

验收：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\unit\test_integration_mcp.py tests\integration\test_mcp_sandbox.py -q
```

预期：`3 passed`，其中集成测试会真实启动独立 stdio MCP 子进程。

本地选择 MCP profile：

```powershell
$env:PROCUREOPS_INTEGRATION_PROFILE="mcp_sandbox"
```

生产只读 MCP 使用 `mcp_readonly` 与 `PROCUREOPS_MCP_CONFIG`。配置文件必须固定三个读取 binding；写工具不会被接受。

## 阶段 3：BM25 + Vector + RRF

- ACL 和 tenant 过滤先于语料统计与排名。
- BM25 与向量各自产生排名，再用 RRF 融合。
- 命中记录包含 `bm25_rank`、`vector_rank`、`rrf_score`、citation 和 document hash。
- 默认 `hashing` 完全离线；`openai_compatible` 仅在显式配置后调用 `/embeddings`。
- 索引时效检查同时绑定 corpus hash、provider、model、dimensions 和 fusion algorithm。

验收：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_rag_evaluation.py
```

当前离线基线预期：6 个案例，`Recall@K=1.0`、`MRR=1.0`、严格 `Precision@K=0.388889`。报告写入 `reports/latest_rag_retrieval_eval.json`。

真实 Embedding 是显式联网路径，配置示例：

```powershell
$env:PROCUREOPS_EMBEDDING_PROFILE="openai_compatible"
$env:PROCUREOPS_EMBEDDING_PROVIDER="your-provider"
$env:PROCUREOPS_EMBEDDING_MODEL="your-embedding-model"
$env:PROCUREOPS_EMBEDDING_BASE_URL="https://provider.example/v1"
$env:PROCUREOPS_EMBEDDING_API_KEY="..."
$env:PROCUREOPS_EMBEDDING_DIMENSIONS="1024"
```

配置后运行索引重建会产生真实 API 调用和可能的费用。

## 阶段 4：证据驱动 Supplier Research

- `supplier_evidence_search` 是 R0 ToolGateway 工具，不允许 Agent 任意访问 URL。
- local 和 allowlisted HTTP connector 都由服务端配置。
- Evidence Judge 校验 tenant、approved supplier、来源、时间、内容哈希、相关性、置信度和 trust tier。
- Judge 拒绝提示注入、当前价格/库存/交期/准入状态、低质量证据和越界供应商。
- 相同 claim 的不同 value 会被标为冲突；补充检索最多 2 次。
- 模型建议只作 advisory，最终仍由 `PreferenceDecisionEngine` 选择。

验收：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\unit\test_research_evidence.py tests\integration\test_enterprise_depth.py -q
```

关键断言：模型建议 `supplier-beta` 时，确定性“总成本”策略仍选择 `supplier-alpha`。

## 阶段 5：Live Model / Holdout 评测

- v2 数据集：development 6、regression 6、locked holdout 6。
- Prompt 候选治理只使用 regression，代码路径不会读取 holdout 做调参。
- 字段检查：part number、quantity、unit、equipment model、allow equivalent。
- 输出：overall、safety、per-tag、per-split、schema failures、P95、tokens、cost、baseline delta。
- 门禁：pass rate、safety rate、P95；失败时报告仍保存，CLI 返回 exit code 2。

只跑离线数据集和门禁测试，不产生费用：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\unit\test_live_model_eval.py tests\integration\test_governed_evolution.py -q
```

真实 regression 评测会产生模型调用费用：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py `
  --split regression --min-pass-rate 0.85 --min-safety-rate 1.0 --max-p95-ms 10000
```

Holdout 必须显式确认，且结果不能用于回改 Prompt：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py `
  --split holdout --confirm-holdout --min-pass-rate 0.85 --min-safety-rate 1.0
```

## 最终离线验收

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

此命令会验证知识清单、重建离线 RAG、运行 Ruff、执行覆盖率门禁和 SQLite 企业深度检查，不调用付费模型。
