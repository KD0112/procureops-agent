# Implementation Status

| 能力 | 状态 | 主要实现 | 自动化证据 |
|---|---|---|---|
| RepoPilot 编码助手 Skill | added, bounded | `codeops/`、`skills/repo_change_review/SKILL.md`、`POST /api/skills/repo-change-review` | 工作区隔离、哈希防覆盖、命令 allowlist、测试门禁、diff 和审批停点 |
| CI 代码问题诊断闭环 | added, bounded | `repo_diagnose_ci`、`POST /api/skills/repo-ci-repair`、`scripts/run_ci_repair_benchmark.py` | 只读日志分类、隔离修复、测试门禁、Diff SHA-256、人工审批停点 |
| Repo 只读 MCP Profile | added, read-only | `scripts/run_repo_mcp_server.py` | `scripts/smoke_repo_mcp.py` 完成 initialize/tools/list/tools/call 验证 |
| Coding-agent Harness 评估 | added | `scripts/run_codeops_benchmark.py`, `data/evals/code_agent_v1.jsonl` | 30 cases；status accuracy/source isolation/blocked-approval precision 均为 1.000 |
| Docker MySQL/Redis/Streams Profile | added, locally accepted | `docker-compose.infra.yml`, `scripts/smoke_infra.py` | WSL 2.7.11 + Docker Server 29.7.2；真实 infrastructure smoke PASS |
| Quality dataset v3 | added | `data/evals/agent_quality_v3.jsonl` | 200 cases, development/regression/holdout, memory/RAG/noise/tool/latency/context coverage |
| Langfuse observability | added, opt-in | `src/procureops/observability/` | API/worker spans, audit mapping, privacy-safe redaction, disabled by default |
| DeepEval adapter | added, opt-in | `evals/deepeval_adapter.py`, `scripts/run_deepeval.py` | answer relevancy, faithfulness and contextual RAG metrics |
| Real CommerceOps quality evidence | added, locally executed | `scripts/prepare_deepeval_commerce.py`, `data/evals/commerce_ops_human_labeled_v1.jsonl` | 5 real DeepSeek outputs；5 条人工审核草案；DeepEval 结果见 `reports/latest_deepeval_commerce_ops.json` |
| Langfuse trace smoke | added, credential-gated | `scripts/run_langfuse_trace_smoke.py` | 当前无 Langfuse 凭据，报告明确记录 `BLOCKED_MISSING_CREDENTIALS`，不伪造云端 trace |
| Quantitative benchmark | added | `scripts/run_quality_benchmark.py`, `reports/latest_quality_benchmark.md` | success, safety, evidence, P50/P95, tool/model calls and cost |
| RAG latency benchmark | added | `scripts/run_rag_latency_benchmark.py` | baseline vs HNSW latency and Recall/Precision/MRR/nDCG |
| Lost-in-the-middle benchmark | added | `scripts/run_lost_middle_benchmark.py` | edge/middle accuracy, middle drop and position-aware packing |
| Harness | 已实现 v1 | `harness/` | 审批、幂等、RBAC、预算、重试、脱敏测试 |
| SQLite 与迁移 | 已实现 | `storage/migrations/` | 租户隔离、乐观锁、持久幂等测试 |
| 任务状态机 | 已实现 | `workflows/state_machine.py` | 合法路径与越级拒绝测试 |
| 成本计算 | 已实现 | `domain/costing.py` | Decimal、四舍五入、库存边界测试 |
| 多格式 Intake | 已实现 | `intake/` | 文本、Excel、PDF、图片/FakeVision 测试 |
| 多附件任务 | 已实现 | Intake Bundle、上传 API、工作台 | 最多 5 个附件、大小门禁、重复行合并、多源证据、冲突人工确认 |
| 任务删除 | 已实现软归档 | API、工作台、迁移 008 | 创建人/合规权限、待处理 Job 终止、附件/证据/PO/审计保留 |
| 单 Agent 闭环 | 已实现 | `agents/single.py` | 暂停审批、恢复、证据、PO 草稿测试 |
| 受治理 RAG | 已实现 SQLite 持久化混合索引 | `rag/`、`knowledge/` | ACL、租户隔离、引用、索引陈旧和动态事实边界测试 |
| 用户记忆 | 已实现安全闭环 | `memory/`、网站记忆中心 | 候选、确认、纠错、删除、TTL、偏好优先级、完整性哈希、访问审计、投毒与策略覆盖拒绝测试 |
| 受治理进化 | 已实现 v2 | `evolution/`、网站进化治理 | 20 条 Gold Set 基线/候选回归、零关键回归、安全率 100% 门禁、合规审批、发布与回滚测试 |
| 回放 | 已实现 | `evals/replay.py` | 哈希验证与篡改检测 |
| 100 条评测 | 已实现 | `data/eval_cases/procurement_e2e_100.jsonl` | 分布锁定测试与完整运行报告 |
| 多 Agent 对照 | 已实现三路实验 | `agents/multi.py`、`agents/llm_supervisor.py` | 同一 100 条数据对比单 Agent、确定性专家、FakeModel 专家；真实模型调用走 Harness |
| 千问文本/视觉 | 已实现路由适配 | `harness/provider_clients.py`、`model_router.py` | DashScope 文本/视觉、Qwen 优先、DeepSeek/Zhipu 降级、熔断与 FakeTransport 测试；本机尚无千问密钥 |
| Supplier Research Agent | 已实现受限循环 | `agents/supplier_research.py` | 最多 3 步、只读物流工具白名单、越权动作降级、确定性最终决策测试 |
| 动态物流 | 已实现 | `logistics_quotes`、`logistics_quote` 工具 | 租户隔离、时效、证据、Decimal 运费覆盖与索引计划测试 |
| 本地身份与职责分离 | 已实现免密码本机模式 | `auth/`、Bearer 会话、网站身份切换 | 自动采购人身份、服务端角色、会话过期/注销、请求头伪造拒绝和 maker-checker 测试 |
| 事务 Outbox | 已实现 | `worker/outbox.py`、迁移 006 | 任务/上传/工作意图原子写入、幂等投递、dispatching 崩溃恢复测试 |
| 第二租户 | 已实现 | `tenant_enterprise_it` Tenant Pack、目录、规则、知识与网站租户切换 | 同一工作流 IT happy path、RAG/目录隔离、API maker-checker 与 20 条跨租户评测 |
| ERP/供应商/物流集成 | 已实现生产形态适配层 | `integrations/`、`run_integration_sandbox.py` | HTTP 鉴权、HTTPS/回环限制、超时与状态码分类、Schema/租户校验、审批哈希、PO 幂等和外部回执投影测试 |
| SSE 任务事件流 | 已实现 | API 持久事件流、前端授权 fetch stream | Last-Event-ID、heartbeat、终态关闭、租户隔离和脱敏测试 |
| 只读 MCP | 已实现可选 Profile | `integrations/mcp.py`、`run_mcp_sandbox.py` | initialize、tools/list、tools/call、只读白名单、租户/Schema 校验和写工具拒绝 |
| BM25 + Vector + RRF | 已实现 | `rag/embeddings.py`、`rag/retrieval.py`、`rag/evaluation.py` | ACL 前置过滤、双路排名、RRF、索引指纹和 6 条检索评测 |
| Advanced RAG pipeline | 已接入 API，可选 | `rag/advanced.py`、`api/app.py`、`runtime.py` | small-to-big、overlap、noise filter、HNSW/IVF-PQ/exact fallback、RRF/rerank、advanced API 回归 |
| Prefetch 证据门禁 | 已实现 | `rag/prefetch.py`、`POST /api/search/prefetch` | 证据不足阻止生成并返回补充查询建议 |
| RAG 调试工作台 | 已实现 | `api/static/retrieval-debug.html`、`POST /api/search/diagnostics` | BM25/vector/RRF/rerank/citation 可视化 payload |
| PDF/OCR/表格保护式解析 | 已实现，可选 OCR | `rag/document_parser.py`、`rag/ingestion.py` | PDF 原生、可选 OCR、DOCX/XLSX/HTML/Markdown 表格 atomic block |
| CommerceOps 业务切片 | 已实现 | `commerce/`、`tenant_commerce_ops`、`/api/commerce/insights` | SQL 白名单、JOIN/退货率、RAG 政策证据、provenance、读写边界 |
| Evidence Judge | 已实现模型研究路径 | `agents/research_evidence.py`、`agents/supplier_research.py` | 来源/时间/哈希/可信层级、冲突、投毒、动态事实拒绝和确定性最终选商 |
| Live Model / Holdout | 已实现 v2 治理 | `evals/live_model.py`、`model_gold_v2.jsonl` | development/regression/locked holdout、质量/安全/P95/Token/成本/基线门禁 |

## 当前限制

- SQLite 是本机 Profile，不等同于生产 PostgreSQL 的 RLS、HA 和连接池。
- 本地 RAG 使用关键词与确定性稠密向量混合评分，保存 Corpus Hash 并检测陈旧；EmbeddingProvider 接口可替换为企业语义向量服务。默认模型用于离线测试，不宣称具备生产语义质量。
- PDF 文本优先本地提取；扫描 PDF 进入 VisionExtractor 回退路径，实际效果取决于配置的视觉模型。
- 真实模型评测不属于普通 CI；API Provider 适配器已实现，但默认命令不产生费用。
- 三路多 Agent 对照当前未展示质量或安全收益，因此默认仍为单 Agent；模型路径保留用于真实样本实验。
- “自主进化”不表示生产自改代码：当前只能形成候选并经过离线门禁与人工发布。
- 本机没有 `DASHSCOPE_API_KEY`，因此没有伪造千问真实分数；Qwen 路由通过 FakeTransport/FakeModel 验证，真实 Gold Set 运行待密钥补齐。
- 外部系统的本机沙箱只证明企业集成契约和故障治理；真实生产连接仍需要目标企业提供 UAT Endpoint、服务账号、网络白名单和字段映射。
- 当前运行状态（2026-08-15）：真实模型开关关闭；已配置 DeepSeek `deepseek-v4-flash` 文本路由和智谱 `glm-4.1v-thinking-flash` 视觉路由，但普通网站验收不会调用它们。Docker/WSL2 本机 Profile 已通过真实 smoke；API 模型通常不是永久免费，是否收费取决于 Provider 套餐。
- Evidence Judge 和有界 Supplier Research 属于 `multi_llm`/独立注入的研究路径；默认 `single` 直接走只读业务工具和确定性选商，不启动模型研究循环。
