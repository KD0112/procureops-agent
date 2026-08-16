# Agent 评估、观测与性能补强

更新时间：2026-08-15

这份文档记录本轮按照“必须加入 + 建议加入、阶段 1-5”完成的内容。NiceEval 没有加入；评估主线采用 DeepEval，生产观测采用 Langfuse，离线回归仍使用项目已有的 Harness 和可重放审计。

## 1. 阶段 1：Langfuse 观测

已加入：

- `src/procureops/observability/langfuse.py`：可选 Langfuse facade、脱敏、审计事件 sink、score 写入。
- API 的 `/api/chat`、`/api/search`：记录 agent/retriever span、缓存命中、结果数量和租户/操作者元数据。
- Worker：把已有不可变审计事件同步映射为低基数 Langfuse span。
- `LANGFUSE_ENABLED=0` 默认关闭；`LANGFUSE_CAPTURE_IO=0` 默认只记录长度和 SHA-256，不记录原始业务输入。
- `pyproject.toml` 增加 `quality` 可选依赖，不安装或没有凭据时主流程仍可离线运行。

启用方式：

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -e ".[quality]"
$env:LANGFUSE_ENABLED="1"
$env:LANGFUSE_PUBLIC_KEY="填入公钥"
$env:LANGFUSE_SECRET_KEY="填入私钥"
$env:LANGFUSE_BASE_URL="https://cloud.langfuse.com"
$env:LANGFUSE_ENVIRONMENT="local"
$env:LANGFUSE_CAPTURE_IO="0"
```

观测原则：Langfuse 负责 trace、span、tool、evaluator score；业务真相仍以数据库、审计事件和 replay 为准。生产环境应继续接入 OpenTelemetry 语义约定、采样、保留期和访问控制，不能把 prompt 原文默认发送到第三方。

## 2. 阶段 2：DeepEval 质量评估

已加入：

- `src/procureops/evals/deepeval_adapter.py`：可选导入和统一 `LLMTestCase` 适配。
- 支持 `answer_relevancy`、`faithfulness`、`contextual_relevancy`、`contextual_precision`、`contextual_recall`。
- `scripts/run_deepeval.py`：读取真实 `actual_output`、`expected_output`、`retrieval_context`，输出 `reports/latest_deepeval.json`，并可把总分写入 Langfuse。

运行输入格式：

```json
{"case_id":"RAG-001","input":"...","actual_output":"...","expected_output":"...","retrieval_context":["..."]}
```

运行命令：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_deepeval.py `
  --input reports\deepeval_input.jsonl `
  --metrics answer_relevancy faithfulness contextual_relevancy
```

注意：本地 Harness 的 `completed` 或 `blocked` 只能证明工作流状态，不等于自然语言答案质量。因此没有把状态字符串伪装成 DeepEval 的 answer relevancy/faithfulness 分数。DeepEval 的真实 judge 运行需要真实模型输出、参考答案/上下文以及可用的评估模型配置。

## 3. 阶段 3：扩展数据集和量化指标

原有 `data/eval_cases/procurement_e2e_100.jsonl` 保持兼容；新增 `data/evals/agent_quality_v3.jsonl`，共 200 条：

| 类别 | 数量 | 用途 |
|---|---:|---|
| normal | 40 | 正常采购主流程 |
| ambiguous | 20 | 澄清和未知目录项 |
| tool_failure | 15 | transient retry 与 permanent fail-closed |
| attack | 15 | prompt injection 与跨租户边界 |
| approval_boundary | 10 | HITL 和角色边界 |
| memory_regression | 25 | 20/40/60/80/100 轮深度标记 |
| rag_noise | 20 | 噪声比例、small-to-big、rerank |
| tool_boundary | 15 | 必须调用与禁止调用工具 |
| queue_failure | 15 | 队列、重试和永久失败 |
| latency_probe | 10 | 并发和延迟探针 |
| context_position | 15 | Lost-in-the-middle 位置实验 |
| **合计** | **200** | development 120 / regression 60 / holdout 20 |

每条新增样本带有 `dataset_version`、`split`、`expected_tools`、`forbidden_tools` 和 `metadata`。当前数据仍以合成/规则回归为主，不能据此声称真实用户满意度；下一步必须加入脱敏真实 query、人工标注的 reference answer、relevance label 和 hard negative。

量化入口：

```powershell
& ".\.venv\Scripts\python.exe" scripts\generate_quality_dataset.py
& ".\.venv\Scripts\python.exe" scripts\run_quality_benchmark.py
```

当前本地一次实测报告：`reports/latest_quality_benchmark.md`。

| 架构 | Success | Safety | Evidence | P50 | P95 | 平均工具调用 | Model calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| single | 1.000 | 1.000 | 1.000 | 440.994 ms | 592.225 ms | 3.38 | 0 |
| multi | 1.000 | 1.000 | 1.000 | 439.446 ms | 580.580 ms | 3.38 | 0 |
| multi_llm / FakeModel | 1.000 | 1.000 | 1.000 | 446.818 ms | 589.793 ms | 3.38 | 1012 |

这些是本地确定性回归值，不是线上 LLM 指标；100% 通过说明当前合成工作流符合预期，也说明数据集还没有足够难的自然语言和模型不确定性。

## 4. 阶段 4：RAG 和 Lost-in-the-middle 实验

### RAG

已有 advanced RAG 现在包括：

```text
治理文档 -> heading-aware parent -> child overlap
-> noise/dedup -> embedding -> HNSW/IVF-PQ/exact fallback
-> BM25 + dense -> RRF -> rerank -> parent expansion
-> tenant/role filter -> top-k -> metrics
```

新增本地 benchmark：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_rag_latency_benchmark.py
```

报告：`reports/latest_rag_latency_benchmark.json`。本次 7 个 query、每个重复 3 次，共 21 次样本，使用相同的 hashing embedding：

| 方案 | P50 | P95 | Recall@5 | Precision@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|---:|
| SQLite BM25 + Vector + RRF | 5.436 ms | 6.720 ms | 0.857 | 0.257 | 0.345 | 0.647 |
| Advanced + Faiss HNSW | 2.975 ms | 3.379 ms | 0.857 | 0.229 | 0.314 | 0.560 |

结论不是“Advanced 全面胜出”：它在这个小 corpus 上降低了延迟，但精度和 nDCG 下降。下一步应扩大 query 集、调 `efSearch/top-k/rerank`，再用真实 embedding 和人工 relevance label 复测。IVF-PQ 依赖足够训练样本，样本不足时必须诚实降级 exact，不能把 fallback 冒充 IVF-PQ。

### Lost-in-the-middle

已有 `day1/project2/context_experiments.py`，新增运行脚本：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_lost_middle_benchmark.py
```

本地位置敏感 reader 的实测是：positions `[0, 1, 4, 7, 8]`，edge accuracy `1.0`，middle accuracy `0.0`，middle drop `1.0`；position-aware packing 将 target 放在位置 `0`，answer accuracy `1.0`。这证明实验指标和 packing 逻辑生效，但不代表真实 LLM 的曲线。真实验收要固定模型、temperature、context 长度、query、needle，分别比较：

1. 原始 ranked order；
2. 直接长窗口拼接；
3. position-aware + small-to-big + rerank。

每个位置至少重复多次，并报告均值、置信区间、edge/middle accuracy、middle drop、token、延迟和成本。

## 5. 阶段 5：推理延迟优化与面试表达

推理延迟不是只有 Transformer 的 `O(n²)`。在线解码通常要拆成：排队、上下文预填充 prefill、逐 token decode、KV cache 读写、工具/数据库等待、网络和序列化。

优先级：

1. 先测量：记录 P50/P95/P99、TTFT、ITL、输入/输出 token、KV cache 命中、tool latency、queue age、GPU 利用率和显存峰值；
2. 再减少无效上下文：三层记忆、small-to-big、rerank、去重、token budget；
3. 再做系统优化：continuous batching、paged KV cache、prefix cache、FlashAttention、异步 I/O、连接池和显存碎片治理；
4. 最后按模型服务能力评估 KV cache quantization、滑窗/压缩、speculative decoding、量化和位置编码扩展；
5. 每次优化必须与同一 query set 做质量回归，避免“延迟降了、答案坏了”。

本项目已实现可记录延迟分解的 `evals/performance.py` 和 RAG benchmark；尚未把 GPU/vLLM 作为默认依赖，因为当前工作区没有稳定的 GPU 推理服务。面试中应说“已建立指标与实验入口，GPU 优化待在目标硬件上实测”，不要虚构吞吐提升。

## 6. 是否需要微调

当前阶段不建议立刻微调基础模型。项目的主要瓶颈仍是数据质量、RAG 召回、上下文组装、工具边界和线上评估，不是模型不会背项目规则。

如果后续确实需要微调，优先顺序是：

- SFT/LoRA：训练稳定的输出格式、工具选择、澄清策略和拒答边界；
- 不微调动态事实：价格、库存、订单状态、权限和交付时间必须由数据库/工具实时提供；
- 数据来源：脱敏真实对话、工具调用 replay、人工修订 bad case、RAG hard negative、攻击/越权样本；
- 每条数据保存 input、context、expected action/answer、tool arguments、policy result、grader label 和 dataset version；
- 按 tenant/time/user 做防泄漏切分，保留 locked holdout；
- 比较 base、prompt-only、RAG、LoRA 四个版本，使用 task success、tool correctness、argument correctness、faithfulness、safety、P95、token/cost 做门禁。

建议达到至少数百条高质量人工/半人工样本后再做第一轮 LoRA；当前 200 条主要是回归骨架，适合证明工程能力，不足以支持“微调提升 X%”的简历数字。

## 7. 必须加入、建议加入、暂不加入

必须加入的已经完成：

- Langfuse 可选 trace/score 与隐私脱敏；
- DeepEval 适配器和真实输入格式；
- 200 条分层数据集、development/regression/holdout；
- success/safety/evidence/P50/P95/tool/model/cost 指标；
- RAG HNSW/IVF-PQ/exact honest fallback、small-to-big、rerank、noise filter；
- Lost-in-the-middle 指标与位置 sweep；
- 真实报告文件和失败问题记录。

建议加入但需要真实环境继续完成：

- 真实 Langfuse dashboard/云端 trace（需要配置 key）；
- 真实 embedding、reranker、LLM 生成输出和 DeepEval 评分；
- 30-100 条人工标注 RAG query 与更大的 holdout；
- OTel exporter、Grafana/Prometheus 或 Langfuse dashboard；
- GPU 上的 vLLM/FlashAttention/KV cache benchmark；
- OIDC/SSO、密钥管理、数据保留和告警策略。

暂不加入：NiceEval、盲目增加第二个 Skill/sub-agent、没有数据门禁的微调、把 LangChain/LangGraph 当作项目卖点。当前重点是协议、工具边界、数据、可观测性和可复现实验；框架只作为实现细节。

## 8. 当前完成度

结论：项目已经基本完成“Agent 开发实习可展示的工程化原型”阶段，尤其是 `project2` 已经具备 API、数据库、缓存、队列、Skill/MCP、RAG、记忆、审计、回放和评估主线。Docker/WSL2 实机验收也已通过；它还不是生产系统，剩余差距主要在真实模型质量、真实用户数据、GPU 性能和安全运维。

简历只写已经有证据的内容：架构能力、测试数量、200 条回归集、真实本地 benchmark 和实验方法；真实 Langfuse/DeepEval 分数、线上 P95、吞吐提升和微调收益，等凭据与报告生成后再写。

## 9. 本轮验收记录和问题修复

- project2：`159 tests collected`，全量测试通过；Ruff 和 `compileall` 通过。
- day1 独立 `.venv` 已按 `requirements.txt` 补齐运行依赖和 pytest/pytest-asyncio，全量为 `54 passed, 5 subtests passed`。
- RAG benchmark 暴露并修复了 lexical score 全为 0 时的除零问题；以后无词命中查询会安全降级，不再 500。
- DeepEval/Langfuse 依赖已安装到 project2 `.venv`，但 DeepEval judge 未运行，因为当前没有评估模型 key；Langfuse 默认关闭，避免无凭据时产生外发请求。
- Docker/WSL2 实机已通过：WSL 2.7.11、Docker Server 29.7.2，`enterprise infrastructure smoke: PASS`；MySQL/Redis/Streams 现在可以标记为本机 Profile 已验收。
