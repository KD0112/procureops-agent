# 生产化 RAG 与 CommerceOps 项目复习

更新时间：2026-08-16

这份文档把本轮补强的“问题—方案—代码证据—指标—边界”串成一条面试主线。目标不是堆更多框架，而是把 `project2` 讲成一个可运行、可观测、可验证的 AI 应用工程原型。

## 一、为什么不再新做第三个孤立 Agent

当前两个项目已经覆盖记忆、RAG、Harness、API、数据库、缓存、队列、Skill/MCP、评估和编码助手闭环。继续新建一个聊天机器人会稀释主线。本轮选择在 `project2` 增加一个独立租户 `tenant_commerce_ops`，用电商运营分析把能力串起来：

```text
用户问题
  -> FastAPI 鉴权/参数校验/Request-ID
  -> 查询意图分类
  -> 只读白名单 SQL + MySQL/SQLite mirror
  -> RAG 退款政策证据
  -> Prefetch 证据门禁
  -> SQL 结果与 citation 合并
  -> Langfuse trace / 审计 / 指标
```

这样既有“真实业务数据结构”，也没有把动态订单事实错误地放进向量库。

## 二、本轮补齐的问题与方案

| 原问题 | 解决方案 | 代码证据 |
|---|---|---|
| advanced RAG 只在 benchmark 中存在 | `baseline/advanced` 两条可选 API pipeline | `src/procureops/runtime.py`、`src/procureops/api/app.py` |
| 只说检索，不知道为什么召回 | 输出 BM25/vector rank、score、RRF、rerank、citation | `POST /api/search/diagnostics`、`static/retrieval-debug.html` |
| 生成前没有证据检查 | Prefetch 先检索；低相关直接返回建议，不创建任务 | `rag/prefetch.py`、`POST /api/search/prefetch`、`ChatRequest.prefetch` |
| PDF/表格切分会破坏结构 | block-aware parser；表格使用 `table:start/end` 保护标记 | `rag/document_parser.py`、`rag/ingestion.py` |
| 扫描 PDF 没有 OCR 回退边界 | 原生文本优先，稀疏页面可选 PyMuPDF + RapidOCR；依赖缺失时记录 warning | `DocumentParser.diagnostics()` |
| API 错误格式不统一 | 稳定 `error_code/message/detail/request_id/retryable/details` 信封 | `api/errors.py` |
| 业务数据没有电商垂直切片 | CommerceOps 产品、订单、区域、退货率、退款政策 tenant pack | `data/tenant_packs/tenant_commerce_ops/` |
| MySQL 只有 schema，没有种子数据入口 | 增加幂等事务种子脚本和 SQL 只读分析方法 | `storage/mysql.py`、`scripts/seed_mysql_commerce.py` |
| 并发/缓存没有数字 | 增加 ASGI API 并发 benchmark，记录成功率、cache hit rate、P50/P95 | `scripts/run_api_concurrency_benchmark.py` |

## 三、RAG 真实链路怎么讲

### 1. 数据处理

文档先经过治理元数据校验，再进入 `DocumentParser`：

```text
PDF/DOCX/XLSX/HTML/Markdown/图片
  -> 原生解析
  -> 结构化 block
  -> 表格保护标记 / OCR warning
  -> approved front matter
  -> heading-aware chunk
```

表格不是普通句子。解析后的表格会保留为一个 atomic block，并写成：

```text
<!-- table:start id=table-1 -->
| SKU | Rule |
|---|---|
| A | 7 days |
<!-- table:end -->
```

当前边界：PDF 原生文字可以离线解析；扫描 PDF OCR 需要安装可选依赖：

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -e ".[documents,ocr]"
```

真实 OCR 质量必须用人工标注的 PDF/表格集评测，不能仅凭依赖安装就宣称生产可用。

### 2. Small-to-big、噪声和索引

`AdvancedRetriever` 的实际顺序：

```text
parent section
  -> child window + overlap
  -> boilerplate/near-duplicate filter
  -> embedding
  -> HNSW 或 IVF-PQ（依赖/数据不足时 exact fallback）
  -> BM25 + dense candidate
  -> RRF
  -> coverage/phrase/rerank
  -> 回填 parent context
  -> tenant/role filter + citation
```

`HNSW/IVF-PQ` 不是无条件开启：小数据集训练 IVF-PQ 会不稳定，系统会记录 `fallback_reason`；Windows 未安装 `hnswlib` 时可以使用 Faiss HNSW。面试时要讲“索引后端和回退原因可观测”，不要把 exact fallback 冒充 ANN。

API 示例：

```json
{
  "query": "退款政策",
  "top_k": 6,
  "pipeline": "advanced"
}
```

### 3. Prefetch 与拒答

Prefetch 是一个生成前的 evidence gate：

- 有授权且达到阈值的 chunk：允许后续 LLM 调用；
- 没有足够证据：`should_call_llm=false`，返回补充型号/订单号/上传文档等建议；
- 动态订单、价格、库存继续只走数据库/工具，RAG 不能替代实时事实。

这比单纯在 prompt 里写“不要幻觉”更容易测试，因为“是否调用 LLM”本身是一个确定性控制点。

## 四、CommerceOps 业务闭环

### 数据模型

```text
commerce_products(tenant_id, product_id, name, category)
commerce_orders(tenant_id, order_id, product_id, region, order_date,
                quantity, unit_price, returned_flag, return_reason)
```

已有主键、外键和索引：租户+订单、租户+日期、租户+商品。查询通过固定模板实现 summary、GMV、区域销售、商品销售和退货率，用户自然语言只用于意图分类，不能直接拼接 SQL。

### API

```text
POST /api/commerce/insights
POST /api/search/prefetch
POST /api/search/diagnostics
GET  /debug/retrieval
```

`/api/commerce/insights` 返回：

- `analytics.rows`：只读 SQL 结果；
- `policy_evidence`：退款政策 RAG citation；
- `prefetch`：是否有足够证据；
- `execution_contract`：SQL 只读、RAG 只作政策证据、writes disabled。

这条链路体现了 SQL 和 RAG 的边界：SQL 回答“发生了什么”，RAG 回答“政策怎么规定”，LLM 只负责受控的解释和整合。

## 五、如何做检索调试工作台演示

启动 API 后打开：

```text
http://127.0.0.1:8030/debug/retrieval
```

输入“退款政策”，展示：

1. Prefetch 是 sufficient 还是 insufficient；
2. authorization filter 后剩多少候选；
3. BM25 rank / score；
4. vector rank / score；
5. RRF 分数；
6. rerank 后分数；
7. parent context 和 citation。

面试时可以故意输入一个知识库没有的问题，展示系统不调用生成、返回补充查询建议。这是比“回答看起来很像”更有说服力的安全演示。

## 六、量化指标与当前结果

### RAG benchmark

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_rag_latency_benchmark.py
```

报告写入 `reports/latest_rag_latency_benchmark.json`，至少记录：

- P50/P95 延迟；
- Recall@5、Precision@5、MRR、nDCG@5；
- duplicate rate；
- requested backend、actual backend、fallback reason；
- child/parent 数量和 noise filter 数量。

当前小型 synthetic corpus 的结果只能证明实验链路可运行。若 advanced 变快但 nDCG 下降，应调 `efSearch/top_k/rerank` 并扩大人工标注 query，不能只拿延迟数字写简历。

### API 并发 benchmark

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_api_concurrency_benchmark.py
```

报告写入 `reports/latest_api_concurrency_benchmark.json`，记录成功率、错误数、cache hit rate、mean/P50/P95/max。它是 ASGI 进程内基准，不包含 TLS、网络、多 worker 和真实 Redis/MySQL 的影响；生产性能必须在 Docker/目标环境重新测量。

### Agent 质量与观测

- DeepEval：离线回答相关性、faithfulness、contextual relevance/precision/recall；必须提供真实 `actual_output` 和 retrieval context；
- Langfuse：trace、retriever/tool span、score 和成本/延迟观测；默认关闭原文捕获并脱敏；
- Harness/replay：业务状态、审批、工具边界和审计真相；
- P50/P95：需要和质量、安全、证据覆盖一起看，不能只优化速度。

## 七、面试中如何回答“数据是 AI 生成的，没说服力吗？”

可以明确说：

> 当前 seed 数据是可公开复现的合成数据，重点不是假装成生产订单，而是展示我如何设计 tenant pack、主键/索引/JOIN/事务、只读 SQL 白名单、RAG 证据边界、异常兜底、缓存和评估。进入真实项目后，我会先用脱敏 query 和人工标注集替换 seed，并保留 locked holdout，避免把测试集结论当线上效果。

后续最有价值的数据补充不是无限扩充 AI 生成文本，而是：

1. 30–100 条脱敏真实 query；
2. 人工标注相关文档、reference answer、拒答标签；
3. 表格/扫描 PDF/噪声/提示注入 hard negative；
4. 按时间、租户、用户切分的 locked holdout。

## 八、尚未虚构的部分

以下需要外部条件，代码已提供边界但本轮不伪造结果：

- 真实 Langfuse 云端 trace：需要公钥/私钥；
- DeepEval judge 分数：需要评估模型配置；
- OIDC/SSO：需要目标企业 IdP；
- 真实 MySQL/Redis 生产压测：需要目标拓扑和数据规模；
- vLLM、FlashAttention、Paged KV、KV cache quantization：需要 GPU/模型服务；
- LoRA：需要足够高质量、非泄漏的数据和稳定评测集。

## 九、完成度结论

当前已经基本完成“Agent 开发实习可展示的工程化原型”：有清晰业务场景、有 API、有数据层、有 RAG 完整链路、有缓存/队列/异步、有 Skill/MCP、有评估/观测、有 RepoPilot 编码助手闭环。

剩余工作不是再做一个大 Agent，而是用真实数据和真实环境补证据：

1. 运行 Docker profile 并保存 `smoke_infra.py` 报告；
2. 配置 Langfuse，截取脱敏 trace；
3. 准备人工标注 query，运行 DeepEval；
4. 在目标硬件上做 GPU 推理延迟实验；
5. 将最终可复现命令、报告和简历数字绑定到 commit。

在这些凭据完成前，简历写“实现了可切换的工程链路和 benchmark”，不要写没有证据的线上吞吐、质量提升百分比或微调收益。
