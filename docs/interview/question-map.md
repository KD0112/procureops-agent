# 面试问题与证据映射

## 为什么先做 Harness？

回答重点：企业 Agent 的主要风险不是模型不会说话，而是越权、重复执行、不可恢复和无法解释。对应证据：`docs/harness-contract.md` 与 `tests/unit/test_tool_gateway.py`。

## 为什么不用 LLM 计算总成本？

回答重点：金额属于确定性事实，必须用 `Decimal` 和固定规则；LLM 只解释结果。后续证据：成本计算器属性测试和最终数据库状态评测。

## 如何防止重复创建采购单？

回答重点：写工具必须提供幂等键，同时绑定请求哈希；同键同请求返回缓存，同键不同请求报冲突。对应测试：`HARN-IDEM-001/002`。

## 如何防止审批后偷换供应商或金额？

回答重点：审批授权的是规范化请求哈希，不是一个通用布尔值。任何关键字段变化都会导致 `subject_hash` 不一致。对应测试：`HARN-APR-001`。

## 为什么旧 RAG 不能直接复制？

回答重点：旧资料以客服话术为中心，且一个长文档混入市场参考价格、物流时效等动态信息；旧索引没有新项目的租户 ACL、审批状态和证据 Schema。新项目只迁移静态领域知识，重新生成治理元数据和独立索引。对应 ADR：`docs/adr/0003-rag-rebuild.md`。

## RAG、数据库和工具如何分工？

回答重点：制度和手册进入 RAG；价格、库存、物流和订单来自工具；规则引擎执行硬阈值。测试 `RAG-GOV-002` 防止边界退化。

## 多 Agent 为什么不是一开始就上？

回答重点：先保留单 Agent 基线，再比较成功率、风险召回、延迟和成本。当前 100 条 A/B 中两者质量和安全相同，多 Agent P95 更高，因此默认单 Agent；这是数据驱动的架构决策，不是主观偏好。

## 没有 Docker 是否还算企业项目？

回答重点：本地 Profile 使用 SQLite 和文件存储，但领域接口、迁移边界和测试不变量按 PostgreSQL/分布式执行设计。是否企业级取决于约束、恢复、审计和可替换性，而不是是否展示 Kubernetes。

## 记忆如何避免“模型记错了”？

回答重点：模型只能提出 candidate；只有用户确认后才可读。记忆按租户和用户隔离，支持纠错链、删除和 TTL；敏感键与审批阈值等硬规则根本不能写入。对应 `tests/integration/test_memory.py`。

## “自主进化”会不会让生产系统失控？

回答重点：这里不是在线自改代码。反馈只能形成带基线版本和来源哈希的候选；候选必须经过离线安全门禁、`compliance_approver` 人审和人工发布，新任务才读取它。每个版本可回滚，采购员不能单独上线。对应 `tests/integration/test_governed_evolution.py`。

## 模型多 Agent 是否真的调用了模型？

回答重点：`multi_llm` 的四个专业 Agent 都通过 Model Gateway 调用模型，并生成预算、重试和审计事件；但是输出只有 advisory 权限。100 条 FakeModel 对照产生 318 次真实 Harness 模型调用，没有质量收益，所以默认仍是单 Agent。对应 `tests/integration/test_llm_supervisor.py` 和 `reports/latest_llm_ab_comparison.json`。

## 如何把 DeepSeek/智谱换成千问？

回答重点：业务模块不绑定 Provider；统一 OpenAI-compatible 适配器读取 `AGENT_TEXT_PROVIDER` / `AGENT_VISION_PROVIDER`。设置为 `qwen` 并提供 `DASHSCOPE_API_KEY` 即可复用同一文本评测和视觉 smoke。当前本机没有千问 Key，所以只声明适配与 FakeTransport 通过，不伪造真实结果。

## 如何回放一次失败任务？

回答重点：回放包冻结 RunContext 版本、工作流事件和工具审计，并对整个 Bundle 与每个事件载荷做哈希验证。预期内的永久故障也生成回放，篡改会导致验证失败。对应 `tests/unit/test_replay.py`。

## 图片或自然语言为什么仍可测试？

回答重点：Intake 依赖统一的 Model Gateway，CI 使用 FakeModel/FakeVision 固定结构化输出；Provider HTTP 适配独立测试，不需要真实 Key，也不会让模型绕过 Schema、审批或工具权限。
