# 真实环境与真实模型证据记录

日期：2026-08-16

本记录区分“真实执行结果”和“因为外部凭据缺失而没有伪造的结果”。

## 1. Docker smoke：通过

执行环境：Docker Desktop + WSL2，Docker Server `29.7.2`。

```powershell
docker compose -f docker-compose.infra.yml up -d
$env:PROCUREOPS_MYSQL_URL="mysql+asyncmy://procureops:procureops-local@127.0.0.1:3307/procureops"
$env:PROCUREOPS_REDIS_URL="redis://127.0.0.1:6380/0"
$env:PROCUREOPS_QUEUE_BACKEND="redis-streams"
& ".\.venv\Scripts\python.exe" scripts\smoke_infra.py
```

结果：`enterprise infrastructure smoke: PASS`。

| 检查 | 结果 |
|---|---|
| MySQL 8.4 health | passed |
| MySQL schema/idempotent init | passed |
| MySQL JOIN | 1 row |
| MySQL transaction + outbox | passed |
| Redis TTL cache | passed |
| Redis Streams publish/claim/ACK | passed |
| FastAPI readiness | Redis + MySQL + Redis Streams all `ok` |

完整机器可读结果：`reports/latest_docker_smoke.json`。

这是本机基础设施 smoke，不代表高可用、灾备或生产压测。

## 2. 少量人工标注 query

标注集：`data/evals/commerce_ops_human_labeled_v1.jsonl`，共 5 条：

- 3 条 regression；2 条 holdout；
- 覆盖退货率、区域销售额、商品销售额、政策证据和 evidence gap；
- 每条包含 expected output、expected intent、gold relevant document IDs；
- `label_status=owner_confirmation_recommended`，表示这是基于当前合成业务数据的人工审核草案，提交简历前应由项目作者再逐条确认。

这样不会把 AI 自生成文本假装成真实用户数据。下一轮应将其中一部分替换为脱敏真实 query，并保留 locked holdout。

## 3. 真实 DeepSeek 输出

执行：

```powershell
& ".\.venv\Scripts\python.exe" scripts\prepare_deepeval_commerce.py
```

脚本从本地 `day1/.env` 指针加载 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`，不会把 key 写入报告。真实模型为 `deepseek-v4-flash`，生成 5 条 `actual_output`，并同时保存本地 CommerceOps API 的 SQL 结果与 RAG citation 作为 `retrieval_context`：

`reports/deepeval_input_commerce_ops.jsonl`

重新生成前先修复了一个真实暴露的意图路由缺陷：

```text
“区域 + 销售额” -> region_sales
“商品 + 销售额” -> product_sales
通用“销售额” -> gmv
```

修复位置：`src/procureops/commerce/analytics.py`，并增加了回归测试。

## 4. 真实 DeepEval 结果

执行：

```powershell
$env:DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE="30"
$env:DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE="120"
& ".\.venv\Scripts\python.exe" scripts\run_deepeval.py `
  --input reports\deepeval_input_commerce_ops.jsonl `
  --limit 5 `
  --metrics answer_relevancy `
  --judge-provider deepseek `
  --output reports\latest_deepeval_commerce_ops.json
```

结果：5 条真实 DeepEval case，4 条得到数值分数，1 条（COPS-001）因远程 judge 返回重试错误记录为 `RetryError`，没有被伪造为 0 分或通过。

| Case | answer relevancy | 状态 |
|---|---:|---|
| COPS-001 | RetryError | 需要复跑/检查 judge JSON 输出 |
| COPS-002 | 0.7143 | scored |
| COPS-003 | 0.7500 | scored |
| COPS-004 | 0.8571 | scored |
| COPS-005 | 0.6667 | scored，低于 0.7 门槛 |

这组结果说明真实评估已经能发现“回答包含无关信息”和“证据不足回答”的质量问题；不能把 4 条均值直接写成线上效果。`faithfulness` 的小规模试跑中，COPS-002 得分为 `1.0`，但由于远程 judge 不稳定没有把它冒充为完整 5 条结果。

## 5. Langfuse trace：当前阻塞，不伪造

检查结果：当前环境没有 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`，也没有启用 `LANGFUSE_ENABLED=1`。因此脚本实际返回：

```text
status: BLOCKED_MISSING_CREDENTIALS
trace_sent: false
```

记录文件：`reports/latest_langfuse_trace.json`。

配置真实 Langfuse 后执行：

```powershell
$env:LANGFUSE_ENABLED="1"
$env:LANGFUSE_PUBLIC_KEY="你的公钥"
$env:LANGFUSE_SECRET_KEY="你的私钥"
$env:LANGFUSE_BASE_URL="https://cloud.langfuse.com"
$env:LANGFUSE_ENVIRONMENT="local"
$env:LANGFUSE_CAPTURE_IO="0"
& ".\.venv\Scripts\python.exe" scripts\run_langfuse_trace_smoke.py
```

成功标准是报告变为 `SENT_TO_LANGFUSE_SDK`，然后在对应 Langfuse project 中人工确认 `smoke.commerce_ops` trace 和 `smoke.evidence_gate` score。密钥只放在本机环境变量，不写入提交或报告。

## 6. 当前结论

- Docker smoke：已真实通过；
- DeepSeek 真实数据：已完成；
- DeepEval 真实评分：已完成小规模真实评分，暴露出 1 个远程 judge 失败样本和 1 个低于阈值样本；
- Langfuse：代码和 smoke 脚本已就绪，但没有凭据，暂不能称为云端 trace 已通过。

因此当前不需要再加新 Agent。下一步只需要配置 Langfuse 凭据后重新执行一次 trace，并由项目作者确认 5 条人工标注即可。
