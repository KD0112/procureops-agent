# 企业级 Agent 基础设施补强复习

本文记录本次把 ProcureOps Agent 从“本地 SQLite + 单机 Worker 原型”补成“可切换企业基础设施 profile”的问题、方案、实现和验收结果。

## 一、改造前问题

| 模块 | 改造前 | 风险 |
|---|---|---|
| API | 已有 FastAPI task API，但没有统一 `/chat`、`/search`、`/documents` | 外部客户端不知道如何区分即时问答、检索和长任务 |
| 业务数据库 | SQLite | 单实例演示可以，不能展示 MySQL 连接池、事务隔离和生产数据库部署 |
| 缓存 | 没有 Redis | Session、工具结果、限流只能存在进程内 |
| 异步 | 上传和 SSE 是异步，Agent/工具主要同步 | 多个独立工具不能并行，阻塞请求线程 |
| 消息队列 | SQLiteWorkQueue + Outbox | 已经有幂等、租约、重试，但还不是多实例共享的 MQ |
| Skill | 有 ToolGateway 和工具，但没有业务 Skill Registry | 工具存在，缺少可复用的业务流程封装 |
| MCP | project2 已有只读 MCP，但默认 profile 是 local | 需要证明 Skill 可以通过 ToolGateway 走 MCP，而不是绕过权限层 |

## 二、改造后架构

```text
Client
  │
  ▼
FastAPI
  ├── /api/chat                → 创建/恢复对话任务
  ├── /api/search              → RAG 检索 + ToolResultCache
  ├── /api/documents           → 上传 → Outbox → RAG ingest job
  ├── /api/tasks/{id}          → 任务状态和审计
  ├── /api/tasks/{id}/events   → SSE
  └── /api/skills/...          → Skill Registry
       │
       ├── MySQL async repository：业务事务、JOIN、索引
       ├── Redis：Session、Tool Cache、TTL、Rate Limit
       ├── RAG：清洗、small-to-big、embedding、索引
       ├── ToolGateway → local / HTTP / read-only MCP
       └── Redis Streams → rag-worker → RAG 构建 → 状态事件
```

默认仍然使用 SQLite 和本地队列，保证离线测试稳定；配置 `PROCUREOPS_MYSQL_URL`、`PROCUREOPS_REDIS_URL` 和 `PROCUREOPS_QUEUE_BACKEND=redis-streams` 后切换到企业 profile。

## 三、FastAPI 统一接口

核心实现：[`src/procureops/api/app.py`](../src/procureops/api/app.py)

新增接口：

| API | 作用 |
|---|---|
| `POST /api/chat` | 接收用户消息，创建 task，并写入带 TTL 的 session cache |
| `POST /api/search` | 独立调用 RAG，返回 citation、score 和 cache hit/miss |
| `POST /api/documents` | 上传文档并创建 RAG ingestion 任务 |
| `GET /api/readiness` | 检查 cache、MySQL、Redis Streams 状态 |
| `GET /api/skills` | 查询已注册 Skill |
| `POST /api/skills/procurement-evidence` | 执行采购证据核验 Skill |

已有 task API 和 SSE 保留不动。长任务仍然返回 `202 + task_id`，前端可以轮询或订阅事件流。

安全约束：

- tenant 从服务端 Actor 中取得，不能信任请求体；
- Chat 和 Search 都经过限流；
- RAG 结果通过 ToolResultCache 按 tenant 隔离；
- 文档只有 `compliance_approver` 可以直接批准进入 RAG corpus；
- API、Skill、MCP 共用 ToolGateway 的权限和审计边界。

## 四、MySQL 业务数据

实现：

- [`src/procureops/storage/mysql.py`](../src/procureops/storage/mysql.py)
- [`src/procureops/storage/mysql_schema.sql`](../src/procureops/storage/mysql_schema.sql)
- [`scripts/init_mysql.py`](../scripts/init_mysql.py)

使用 SQLAlchemy AsyncEngine + `asyncmy`。核心表包括：

```text
tenants
products
suppliers
inventory
quotations
procurement_tasks
outbox_events
```

已体现：

- 复合主键：`(tenant_id, product_id)`；
- 外键：库存、报价、供应商和租户之间的引用；
- 联合索引：租户 + 商品、租户 + 状态、报价检索；
- JOIN：商品、供应商、库存、报价联合查询；
- 事务：创建采购任务和 Outbox 事件必须一起提交；
- 连接池和 `pool_pre_ping`；
- 健康检查：`SELECT 1`。

当前没有把全部 SQLite Repository 强行重写成 MySQL，而是先完成一个真实业务垂直切片。这样既保留离线回归，又能在面试中展示真实 MySQL 设计。

启动：

```powershell
docker compose -f docker-compose.infra.yml up -d
& ".\.venv\Scripts\python.exe" -m pip install -e ".[infra]"
$env:PROCUREOPS_MYSQL_URL="mysql+asyncmy://procureops:procureops-local@127.0.0.1:3307/procureops"
& ".\.venv\Scripts\python.exe" scripts\init_mysql.py
```

## 五、Redis Session、缓存和限流

实现：[`src/procureops/infrastructure/cache.py`](../src/procureops/infrastructure/cache.py)

已实现：

- `SessionStore`：Session TTL；
- `ToolResultCache`：工具结果 JSON 缓存；
- `RateLimiter`：租户 + 用户维度的窗口计数；
- Redis 不可用时使用确定性的 `InMemoryAsyncCache`，不影响离线测试；
- 所有 key 包含 tenant，防止跨租户缓存污染。

Key 形式：

```text
procureops:tenant:{tenant_id}:session:{session_id}
procureops:tenant:{tenant_id}:tool:{tool_name}:{request_hash}
procureops:tenant:{tenant_id}:rate:{actor_id}
```

缓存八股对应工程实现：

| 问题 | 方案 |
|---|---|
| 缓存穿透 | 空结果短 TTL，且查询前校验输入 |
| 缓存击穿 | 后续可加入 Redis lock；当前 key 和 TTL 已集中封装 |
| 缓存雪崩 | 生产环境给 TTL 增加随机抖动 |
| 跨租户污染 | key 强制包含 tenant_id |
| 写后脏数据 | 业务写入成功后删除相关 Tool Cache |

## 六、异步编程

实现：[`src/procureops/harness/async_execution.py`](../src/procureops/harness/async_execution.py)

`AsyncToolExecutor` 提供：

- `asyncio.gather` 并行独立只读调用；
- semaphore 并发上限；
- timeout；
- fail-fast 或收集单个失败结果；
- 同步工具使用 `asyncio.to_thread`，避免阻塞 FastAPI event loop。

正确的并行边界：

```text
商品查询、供应商查询、物流查询：可并行（没有依赖时）
扣库存、提交订单、写 PO：必须幂等并受事务/审批控制
```

本次 Skill 中的商品 → 供应商 → 物流是有数据依赖的顺序链，因此没有为了“看起来异步”而错误并行；Skill 通过 `from_tool_gateway()` 把同步 ToolGateway 放入线程池，API 线程不会被阻塞。

## 七、消息队列和长任务

实现：

- [`src/procureops/infrastructure/streams.py`](../src/procureops/infrastructure/streams.py)
- [`scripts/run_rag_stream_worker.py`](../scripts/run_rag_stream_worker.py)
- [`src/procureops/rag/ingestion.py`](../src/procureops/rag/ingestion.py)

文档流程：

```text
POST /api/documents
  → MySQL/SQLite 任务 + Outbox 原子写入
  → Redis Stream rag:ingest
  → Consumer Group rag-workers
  → worker claim
  → OCR/文本提取/治理 Markdown
  → approved 文档重建 RAG 索引
  → ack
  → 任务事件记录
```

实现了：

- publish；
- consumer group；
- claim；
- ACK；
- 重试；
- dead-letter stream；
- worker 脚本；
- 没有 Redis 时的内存队列测试 fallback。

默认上传文档进入 `staged_for_approval`，不会直接污染检索库。只有合规角色批准后才会进入 governed knowledge corpus。

## 八、Skill 与 MCP

Skill 文件：[`skills/procurement_evidence/SKILL.md`](../skills/procurement_evidence/SKILL.md)

执行代码：[`src/procureops/skills/procurement_evidence.py`](../src/procureops/skills/procurement_evidence.py)

Skill 名称：`procurement_evidence`

流程：

```text
query
  → catalog_lookup
  → supplier_lookup
  → logistics_quote
  → 结构化 evidence result
```

Skill 不直接访问 MySQL，也不直接调用 MCP，而是：

```text
Skill → ToolGateway → local / HTTP / MCP
```

这一区别很重要：

- Tool 是一个动作；
- MCP 是远程 Tool 协议；
- Skill 是带输入/输出/权限/失败处理/验收标准的业务流程。

当前 MCP 仍然只读，采购订单写入继续走原有审批和幂等状态机。

## 九、测试与验收

新增测试：

- [`tests/unit/test_infrastructure.py`](../tests/unit/test_infrastructure.py)
- [`tests/integration/test_new_api_contracts.py`](../tests/integration/test_new_api_contracts.py)

覆盖：

- Session tenant isolation；
- Tool Cache tenant isolation；
- TTL/限流；
- Stream claim/ACK/DLQ；
- async 并发执行；
- Skill Registry 和结构化输出；
- `/api/chat`；
- `/api/search`；
- `/api/readiness`；
- `/api/skills`；
- 原有 RAG index 回归。

已执行通过：

```text
10 个新增/重点测试通过
project2 全量：159 tests collected，全部通过
原有 RAG index tests passed
advanced RAG tests passed
Ruff check passed
Python compile check passed
```

当前测试使用内存 cache/queue 和 SQLite 离线 profile。真正 MySQL、Redis、Redis Streams 的连接验收需要启动 Docker Compose 后再执行：

```powershell
docker compose -f docker-compose.infra.yml up -d
& ".\.venv\Scripts\python.exe" scripts\init_mysql.py
$env:PROCUREOPS_QUEUE_BACKEND="redis-streams"
& ".\.venv\Scripts\python.exe" scripts\run_rag_stream_worker.py --loop
```

## 十、面试回答模板

### 为什么没有直接把 SQLite 全部换成 MySQL？

我保留 SQLite 作为完全离线的回归 profile，新增 SQLAlchemy AsyncEngine 的 MySQL business adapter，先完成采购任务、商品、供应商、库存和 Outbox 的真实事务切片。这样既能展示 MySQL 主键、索引、JOIN、事务和连接池，又不会让离线测试依赖外部服务。

### 为什么使用 Redis Streams，而不是直接 Kafka？

当前任务规模是文档 ingestion 和 RAG rebuild，先用 Redis Streams 完成 consumer group、ACK、重试和 DLQ，学习成本和本地部署成本更低。吞吐、跨地域复制和长时间日志保留达到要求后，再评估 Kafka。

### Skill 和 MCP 的关系是什么？

Skill 是业务流程，MCP 是 Tool 的协议。`procurement_evidence` Skill 只描述采购证据核验流程，具体商品、供应商、物流查询仍通过 ToolGateway 执行；ToolGateway 再选择 local、HTTP 或只读 MCP transport，同时负责租户、角色、审计和失败关闭。

### 文档上传为什么默认不直接进入 RAG？

上传内容是不可信输入，可能包含错误事实或提示注入。系统先提取和治理，进入 staging；合规角色批准后才生成 approved front matter 并重建 governed index。

## 十一、后续补充

已经基本完善为“企业级 Agent 原型”，但还不是生产系统。后续按优先级：

1. 在真实 Docker MySQL/Redis 上跑一次端到端验收并保存报告；
2. 给 MySQL adapter 增加 `EXPLAIN` 和并发事务测试；
3. Redis 增加分布式锁、TTL jitter 和缓存失效测试；
4. Redis Streams 增加 pending reclaim、消费者宕机恢复和重试上限；
5. 将更多外部 HTTP/MCP 工具改成真正 async client；
6. 增加 OpenTelemetry、队列 lag、cache hit、P95、token/cost 指标；
7. 根据真实 query 集比较 baseline RAG 与 advanced RAG；
8. 需要多角色上下文隔离时，再增加第二个 Skill 或 sub-agent，不要盲目拆多 Agent；
9. 生产部署使用 OIDC/SSO、密钥管理、数据库备份和对象存储，不使用 local-session 演示身份。

## 十二、CommerceOps 与本轮 RAG 补强（2026-08-16）

本轮在不破坏离线 profile 的前提下增加了 `tenant_commerce_ops` 垂直切片。它使用固定白名单 SQL 查询订单/商品/区域/退货率，再使用 RAG 查询退款政策；SQL 负责动态业务事实，RAG 负责版本化政策证据，二者通过 `/api/commerce/insights` 合并，并明确 `writes=disabled`。

RAG 现在提供 `baseline` 和 `advanced` pipeline。advanced pipeline 已接入 runtime/API，包含 small-to-big、overlap、noise filter、HNSW/IVF-PQ/exact fallback、BM25/vector/RRF/rerank、Prefetch 证据门禁和 `/debug/retrieval` 诊断台。文档解析增加 PDF 原生 + 可选 OCR、DOCX/XLSX/HTML/Markdown 表格保护式 block。

新增验收命令：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_api_concurrency_benchmark.py
& ".\.venv\Scripts\python.exe" scripts\seed_mysql_commerce.py
```

本次代码验收已通过全量 pytest、Ruff、compileall、知识库 manifest、RAG benchmark 和 API 并发 benchmark。2026-08-16 重启后的 Docker 重验中，Docker Desktop 曾短暂报告 Server `29.7.2`，但执行 `docker compose up -d` 时 Linux engine 返回 EOF 并随后停止；因此本轮没有把新的 MySQL/Redis smoke 标记为通过。此前历史记录中的本机 smoke 结果仍保留，但如需最新证据，应重新启动 Docker Desktop 后执行 `scripts\smoke_infra.py`。

简历中可以写“实现可切换 SQLite/MySQL、内存/Redis、SQLite Queue/Redis Streams 的企业 Agent profile”，但只有完成 Docker 真服务验收和性能记录后，才写具体延迟、吞吐或命中率提升数字。
## 历史安装过程（重启前）

本次已完成 Docker Desktop 的安装尝试和代码侧验收：

- Docker Desktop `4.86.0` 已安装，Docker CLI `29.7.2`，Compose `v5.3.1`。
- 初次启动失败的直接原因由 Docker 日志确认：`WSL is not installed`。
- 已通过管理员 UAC 启用 `Microsoft-Windows-Subsystem-Linux` 和 `VirtualMachinePlatform`；DISM 返回 `3010`，表示需要重启后生效。
- 当时机器仍需重启，并在重启后再次检查 WSL2 与 BIOS/UEFI 虚拟化；在重启前 Docker daemon 无法启动，因此当时没有伪造 MySQL/Redis 的真实通过结果。
- 已安装项目的 `[infra]` 依赖：`SQLAlchemy`、`asyncmy`、`redis`、`alembic`。
- RAG 原生依赖已安装 `numpy` 和 `faiss-cpu`；Windows 当前缺少 MSVC Build Tools，`hnswlib` 无法编译，因此 `backend="hnsw"` 已实现 Faiss HNSW fallback，`ivf-pq` 使用 Faiss，数据量不足时会明确回退 exact。
- 新增 [`scripts/smoke_infra.py`](../scripts/smoke_infra.py)，一次覆盖 MySQL 建表、幂等种子数据、事务 + Outbox、Join 查询、Redis TTL、Redis Streams claim/ACK 以及 FastAPI readiness。

重启后执行真实环境验收：

```powershell
cd "D:\new things\项目1\project2"
docker info
docker compose -f docker-compose.infra.yml up -d
docker compose -f docker-compose.infra.yml ps
$env:PROCUREOPS_MYSQL_URL="mysql+asyncmy://procureops:procureops-local@127.0.0.1:3307/procureops"
$env:PROCUREOPS_REDIS_URL="redis://127.0.0.1:6380/0"
$env:PROCUREOPS_QUEUE_BACKEND="redis-streams"
& ".\.venv\Scripts\python.exe" scripts\smoke_infra.py
```

验收判定：

- 历史离线自动化回归：`149 passed`，Ruff 通过，Python 编译检查通过。
- 历史验收判定：等待重启后运行 `smoke_infra.py`；只有该脚本输出 `enterprise infrastructure smoke: PASS`，才把 MySQL/Redis/Streams 标记为实机通过。
- 当前项目可以称为“企业 Agent 工程化原型基本完成”，但不能把 Docker daemon 尚未启动的状态描述成生产就绪。

### 最终状态更新（2026-08-15）

上面的“等待重启”是历史记录，已由本次实机验收关闭：WSL 2.7.11 与 Docker Server 29.7.2 已正常运行，`mysql:8.4` 和 `redis:7.4-alpine` 容器已启动。设置 MySQL、Redis 和 Redis Streams 环境变量后，`scripts/smoke_infra.py` 输出 `enterprise infrastructure smoke: PASS`，真实覆盖 schema、JOIN、事务 Outbox、Redis TTL、Streams claim/ACK 和 API readiness。当前可以把 MySQL/Redis/Streams 标记为本机 Profile 已验收，但仍不能称为生产部署。

本轮还修复了 smoke 与实现接口不一致、MySQL 8.4 弃用语法和 `BOOLEAN` display-width 警告。project2 全量测试、Ruff、compileall 通过；day1 独立环境为 `54 passed, 5 subtests passed`。
