# Project2 核心概念与白话验收

本文用于解释 Project2 中容易混淆的六个概念，并记录 2026-08-09 完成的阶段 1-5 改造。它既是学习文档，也是向面试官或业务人员演示时的讲解稿。

## 0. 先记住 Project2 的总体定位

Project2 不是一个“模型收到问题后直接回答”的聊天机器人，而是一个采购任务系统：

```text
用户提交采购需求
  -> 解析采购明细
  -> 检索静态制度和产品知识
  -> 通过工具查询目录、报价、库存和物流
  -> 确定性计算成本与选择供应商
  -> 高风险动作等待人工审批
  -> 审批后生成唯一 PO 草稿
```

LLM 只处理自然语言、图片和咨询性判断。价格、库存、金额、权限、审批和最终写入不由模型决定。

## 1. SSE 实时事件流是什么

### 白话解释

SSE 可以理解成“采购任务的物流轨迹”。任务每完成一个公开步骤，后端就把一个事件推送给浏览器：

```text
任务已进入队列
  -> 已解析采购行
  -> 已检索知识
  -> 已匹配目录
  -> 已查询供应商
  -> 等待审批
  -> 已生成 PO 草稿
```

它展示的是业务状态和工具执行结果，不是模型的隐藏思维过程，也不暴露 chain of thought、Prompt、Token 或密钥。

### 企业里对应什么

- 订单履约和快递轨迹；
- 数据处理任务的进度条；
- CI/CD 发布流水线状态；
- 长时间后台任务的运行事件；
- 审批、风控和审计事件流。

### Project2 的实现

- 端点：`GET /api/tasks/{task_id}/events/stream`；
- 事件来自 SQLite 中已经持久化的 `workflow_events.sequence`；
- 支持 `Last-Event-ID`，断线后可以从最后一个事件继续；
- 15 秒 heartbeat；
- Bearer 鉴权和租户隔离；
- 任务切换时前端取消旧连接；
- 对 prompt、reasoning、token、credential 等字段递归脱敏。

### 怎么验收

1. 打开网站并创建任务；
2. 不刷新页面，点击“运行下一项 Worker”；
3. 查看右侧时间线是否自动新增事件；
4. 运行自动测试：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\integration\test_api.py -k event_stream -q
```

这一能力主要参考 DeepResearch / CloudAgent 的阶段事件流，但 Project2 不暴露模型思维链。

## 2. MCP 与 Tool Gateway 是什么

### 两者不是同一个东西

Tool Gateway 是企业内部的“工具安检和权限中心”。MCP 是工具服务器和 Agent 之间的一种标准协议。

```text
Agent 提出工具请求
  -> Tool Gateway 检查租户、角色、风险、预算和参数
  -> Integration Suite 选择 local / HTTP / MCP 适配器
  -> MCP 执行 initialize、tools/list、tools/call
  -> 返回值通过 Schema 和租户校验
  -> 结果写入证据和审计
```

模型可以提出“我想查询供应商”，但它不能绕过 Tool Gateway，也不能自己提供服务器地址、Shell 命令或任意工具名。

### 当前网站是否正在使用 MCP

Tool Gateway 始终启用；当前网站默认集成 Profile 是 `local`，所以默认查询本地企业投影，不经过 MCP 进程。

设置下面的 Profile 后，读工具才会通过 MCP transport：

```powershell
$env:PROCUREOPS_INTEGRATION_PROFILE="mcp_sandbox"
```

当前 MCP 只允许三个只读工具：

- `catalog_lookup`；
- `supplier_lookup`；
- `logistics_quote`。

`purchase_order_draft` 不允许通过 MCP 写入，会 fail closed。写入仍必须经过 Project2 自己的审批和幂等流程。

### 相比 Toy Project 的区别

Toy Project 常见做法是把 Python 函数列表直接交给模型，模型给出参数就执行。Project2 多了：

- 服务端工具白名单；
- 每个工具的风险等级和动作类型；
- 服务端租户与角色检查；
- 模型/工具调用预算；
- 参数和返回值 Schema；
- 分类重试和失败关闭；
- 审计、证据和关联 ID；
- 写操作审批与幂等。

Day1 主要是知识库客服和 RAG，没有 Project2 这条统一的 `Agent -> Tool Gateway -> Integration -> MCP/HTTP/local` 企业工具调用边界。

### 怎么验收

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\unit\test_integration_mcp.py tests\integration\test_mcp_sandbox.py -q
```

测试会真实启动 stdio MCP 子进程，验证初始化、工具发现、只读调用、租户校验和写工具拒绝。

这一能力主要参考 LangGraph + MCP 出行助手的 MCP 协议和工具配置思想，但没有用 LangGraph 替换 Project2 已有的权威状态机。

## 3. Project2 的 RAG 文档是什么

### 文档来源

当前 `knowledge/` 下有 10 份 Markdown 文档，内容包括：

- 工程机械采购、审批、证据、报价时效、供应商和记忆治理制度；
- 工程机械目录匹配和合成兼容性说明；
- 企业 IT 采购制度和目录指南。

这些是为了 Project2 演示而编写和整理的合成企业知识，不是真实公司的内部文件，也不是从用户提供的四个网页项目复制的内容。四个参考项目影响的是架构选择，不是知识文档内容。

### 是否已经导入系统和网页

已经进入系统，但不是把 Markdown 原文直接嵌进网页：

```text
knowledge/*.md
  -> 元数据、状态、租户和角色校验
  -> 文档切块
  -> BM25 排名
  -> Embedding 向量排名
  -> RRF 融合
  -> 返回 citation、document hash 和排名
  -> 作为采购任务的 RAG 证据显示在网页证据链
```

服务启动时会检查知识清单和索引指纹。文档、Embedding Provider、模型、维度或融合算法变化时，旧索引会被判为陈旧并重建。

### 大模型是否基于这些文档回答问题

不能简单理解为“模型基于文档回答聊天问题”。Project2 不是问答网站：

- RAG 给采购工作流补充静态制度和产品指南；
- 网页展示命中的证据和引用；
- 默认单 Agent 和离线模式不需要调用真实 LLM；
- 启用真实模型后，模型只能接收受控的检索摘要；
- 当前价格、库存、物流、准入状态和订单状态禁止从 RAG 获取，只能走工具。

### BM25、Embedding、RRF 分别做什么

- BM25：擅长精确关键词，例如零件号、制度名称；
- Embedding：擅长意思相近但用词不同的查询；
- RRF：把两个排名合并，避免只依赖一种检索方式。

默认 Embedding 是 `feature-hashing-v1`，完全离线、免费、可复现，但语义质量不等于真实企业 Embedding。显式配置 `openai_compatible` 后才会调用真实向量服务。

### 相比 Day1 和 Toy Project

Day1 已经有 Chroma、BGE 中文 Embedding、向量排名、引用和低置信保护；它在默认语义向量质量上可能强于 Project2 的离线 hashing Embedding。

Project2 的优势不是“Embedding 一定更准”，而是：

- 先按 tenant 和 ACL 过滤，再统计和排序；
- BM25 与向量使用独立排名并通过 RRF 融合；
- 明确区分静态知识和动态业务事实；
- 每个命中带 citation、版本和 SHA-256；
- 有独立检索评测集和索引陈旧检查；
- RAG 结果进入任务证据链，而不是只生成一段聊天答案。

### 怎么验收

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_rag_evaluation.py
```

当前离线基线：6 个案例，`Recall@K=1.0`、`MRR=1.0`、严格 `Precision@K=0.388889`。

这一能力同时参考 LangChain 知识库助手的 Retriever/Embedding 分层，以及 DeepResearch 的混合检索、来源和证据思想。

## 4. Evidence Judge 与 Supplier Research 是什么

### 白话类比

可以把它们想成三个人：

1. Supplier Research 是调研员，负责收集供应商能力资料；
2. Evidence Judge 是审稿人，决定哪些资料可以进入报告；
3. PreferenceDecisionEngine 是财务/采购规则，负责最终选商。

调研员可以建议供应商 B，但如果确定性总成本规则计算出供应商 A 更合适，系统最终仍选 A。

### 它防止的不只是模型偶然犯错

它同时处理：

- 模型幻觉；
- 过期资料；
- 提示注入；
- 跨租户证据；
- 未准入供应商；
- 重复和互相冲突的资料；
- 把宣传资料误当成当前价格、库存或交期。

### Evidence Judge 根据什么审查

每条供应商证据至少包含：

- 当前 tenant；
- product 和 supplier；
- source ID 和 source type；
- observed_at；
- content hash；
- relevance 和 confidence；
- trust tier；
- claim key 和 claim value。

Judge 会拒绝来源缺失、哈希不匹配、低相关、低置信、越界供应商和包含注入指令的证据。相同 claim 出现不同 value 时会标记冲突，并允许最多 2 次补充检索。

价格、库存、当前交期和供应商准入状态即使出现在研究资料里也不能作为最终事实，必须由受控业务工具重新查询。

### 当前什么时候会运行

默认 `single` 模式直接使用只读供应商/物流工具和确定性选商规则，不启动模型研究循环。

选择 `multi_llm`，并显式启用真实模型后，才会创建 `BoundedSupplierResearchAgent`，执行 Evidence Judge 和最多 3 步的模型研究。测试也可以独立注入该组件进行离线验收。

### 怎么验收

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\unit\test_research_evidence.py tests\integration\test_enterprise_depth.py -q
```

最重要的断言不是“模型推荐正确”，而是“模型推荐错误也不能改变最终受控决策”：模型推荐 `supplier-beta` 时，总成本策略仍选择 `supplier-alpha`。

这一能力主要参考 DeepResearch / CloudAgent 的 Evidence Judge 和有界补充检索。

## 5. Live Model 与 Holdout 评测是什么

### 白话类比

把模型和 Prompt 当作学生：

- development 是练习题，可以边做边改；
- regression 是月考，每次修改 Prompt 后都要考，防止旧能力退步；
- holdout 是密封的期末考试，不能拿它反复调答案。

如果一直用同一批题调 Prompt，最后只证明模型背会了题目，不能证明它遇到新采购需求仍然可靠。

### 评测检查什么

- 零件号、数量、单位、设备型号和等效件偏好；
- 正常样本和 Prompt Injection 等安全样本；
- JSON/Schema 是否有效；
- 总通过率和安全通过率；
- P95 延迟；
- Token 和成本；
- 相比旧基线是否退化。

### 为什么还要加载真实模型

离线 FakeModel 的作用是验证代码、Schema、预算、失败分支和评测门禁本身正确。它不能证明 DeepSeek、GLM 或 Qwen 在真实自然语言上的质量。

真实模型会受到措辞、网络、Provider、模型版本、延迟和输出格式波动影响，所以生产前必须单独运行真实 regression 和最后的 locked holdout。真实评测是显式、可能收费的路径，不属于普通 CI。

### 当前模型状态（2026-08-09）

- `PROCUREOPS_ENABLE_LIVE_MODELS=false`，当前网站不会调用真实模型；
- 文本路由已配置：DeepSeek `deepseek-v4-flash`；
- 视觉路由已配置：智谱 `glm-4.1v-thinking-flash`；
- 如果以后配置 `DASHSCOPE_API_KEY`，Qwen 可以成为文本和视觉首选；
- DeepSeek 和 GLM API 不能笼统称为免费模型，是否收费取决于 Provider 套餐和额度；
- 普通测试使用 FakeModel，RAG 默认使用离线 hashing embedding，因此普通验收不产生模型费用。

### 怎么验收

不花钱的门禁测试：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests\unit\test_live_model_eval.py tests\integration\test_governed_evolution.py -q
```

真实 regression 和 holdout 只有在确认 API Key、预算和数据集后才运行，命令见 `docs/phase-1-5-acceptance.md`。

这部分吸收了 Vibe Coding 的自动质量门禁思想，并加入企业模型治理所需的独立 Holdout。

## 6. Project2 现在是不是多 Agent

Project2 同时实现三种模式，但默认不是多 Agent：

| 页面选项 | 实际含义 | 是否调用真实模型 | 是否拥有最终决策权 |
|---|---|---:|---:|
| `single` | 一个权威采购工作流调用受控工具 | 默认否 | 是 |
| `multi` | Supervisor + 4 个确定性专业审阅器 | 否 | 否，审阅结果只进入 trace |
| `multi_llm` | Supervisor + 4 个模型专业审阅阶段 + Supplier Research | 是，必须显式开启 | 否，仍由权威工作流决策 |

四个专业阶段是：

- `intake_agent`：检查采购输入是否完整；
- `catalog_matcher`：检查目录候选；
- `supplier_research_agent`：检查准入供应商选项；
- `policy_risk_agent`：检查金额、证据和审批角色。

这些专业 Agent 负责各自类别的审阅，但不会各自修改数据库或绕过审批。三种架构使用相同状态机、工具、RAG、审批和幂等实现，才能公平 A/B。

当前 100 条合成数据对照没有证明多 Agent 带来质量或安全收益，因此生产默认保留更简单的 `single`。这不是功能缺失，而是评测驱动的架构选择。

## 7. 四个参考项目与 Project2 的对应关系

| 参考项目 | Project2 对应能力 | Project2 的进一步约束 |
|---|---|---|
| LangChain 知识库助手 | Retriever、Embedding、知识切块和检索 | BM25 + Vector + RRF、ACL、租户、哈希、检索评测、静态/动态事实边界 |
| LangGraph + MCP 出行助手 | MCP initialize/list/call、工具服务器配置 | Tool Gateway 先做权限/风险/预算检查，不用 InMemorySaver 代替持久状态机 |
| Vibe Coding 指南 | 规则、测试、文档、质量门禁 | 142 个测试、90% 覆盖率门禁、回归/Holdout、实现与文档同步 |
| DeepResearch / CloudAgent | SSE、Evidence Judge、有界研究、混合检索 | 不暴露思维链、不无限反思、模型建议不能越过确定性决策和审批 |

## 8. 2026-08-09 修改记录

今天完成并写入 Project2 的改造包括：

1. 新增持久化 SSE 事件流、断线续传、heartbeat、终态关闭、鉴权、租户隔离和敏感字段脱敏；
2. 前端使用带 Authorization 的 `fetch` stream，移除 4 秒整页轮询；
3. 新增只读 stdio MCP transport、MCP 沙箱、服务端配置绑定和写工具 fail-closed；
4. RAG 改为标准 BM25、向量排名和 RRF 融合，新增可替换 Embedding Provider、索引指纹和检索评测；
5. 新增 Supplier Research Evidence Judge、allowlisted connector、冲突/投毒检测和最多 2 次补充检索；
6. 新增 development/regression/locked holdout 数据集和 Live Model 质量、延迟、Token、成本及基线门禁；
7. 修复新建任务“取消”按钮误提交；
8. 修复静态资源缓存版本错配和侧栏长标题横向溢出；
9. 修复含多个逗号的自然语言被误识别为 CSV 并进入重试的问题；
10. 新增阶段 1-5 验收文档、RAG 报告和针对上述能力的自动化测试。

最终离线验证结果：

- `142 passed`；
- 覆盖率 `90.01%`；
- Ruff 通过；
- SQLite integrity `ok`；
- 外键违规 `0`；
- 10 份知识文档、43 个 chunk；
- RAG `Recall@K=1.0`、`MRR=1.0`、严格 `Precision@K=0.388889`；
- 浏览器创建任务、取消、Worker、SSE 时间线和布局验收通过；
- 未运行新的付费 Live Model 或 locked holdout 评测。

## 9. 推荐的十分钟展示顺序

1. 先说明 Project2 是任务系统，不是聊天框；
2. 创建 `single` 任务并运行 Worker，展示采购明细、RAG 证据和 SSE 时间线；
3. 切换审批人，展示 maker-checker 和审批后 PO；
4. 用 Prompt Injection 样本展示工具和审批边界；
5. 运行 RAG 评测并解释 BM25、Embedding、RRF；
6. 运行 MCP 测试，说明 MCP 是可替换 transport，Tool Gateway 才是权限中心；
7. 运行 Evidence Judge 测试，说明“模型建议错误也不能改变最终选商”；
8. 展示 regression/holdout 分层，说明真实模型必须独立考试；
9. 展示 A/B 报告，说明多 Agent 没有收益所以默认单 Agent；
10. 主动说明 SQLite、本地身份和合成数据是本机 Profile，不冒充生产系统。

最终完整验收：

```powershell
cd "D:\new things\项目1\project2"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```
