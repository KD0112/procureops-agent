# Offline Evaluation Results

## 最新完整运行

- 数据集：100 条合成采购任务
- 单 Agent：100/100 结果符合预期，安全门禁 100%
- 多 Agent：100/100 结果符合预期，安全门禁 100%
- 模型多 Agent（FakeModel）：100/100 结果符合预期，安全门禁 100%，450 次 Harness 模型调用，费用 0
- 完成任务字段证据覆盖率：100%
- 普通 CI 模型调用：0
- 普通 CI 估算模型费用：0 USD

结果分布：

| 结果 | 数量 | 含义 |
|---|---:|---|
| completed | 66 | 正常、恢复性工具错误、Prompt 注入和审批边界任务完成 |
| needs_input | 20 | 模糊目录请求被要求补充信息 |
| tool_failure | 7 | 永久工具故障 fail-closed |
| blocked | 7 | 跨租户参数被 AuthorizationDenied 阻断 |

失败分类中包含 7 个 `PermanentToolError` 和 7 个 `AuthorizationDenied`。它们是测试设计中的预期安全结果，不是未处理异常。

## A/B 决策

三种架构使用相同数据快照，并按奇偶案例反转执行顺序以抵消热缓存偏差。质量、安全、工具调用和证据覆盖相同；模型多 Agent（包含受限供应商研究循环）增加 450 次模型网关调用但没有质量收益。因此推荐 `prefer_single_agent`：默认运行单 Agent，保留 Supervisor 和专业 Agent 边界作为实验路径。

## 解释边界

这是合成数据上的 Harness、流程和安全不变量评测，不能解释为真实企业采购准确率。真实准确率需要脱敏业务样本、人工标注目录真值和独立真实模型评测集。这个限制应在面试中主动说明。

## 第二租户与集成增量结果

- 数据集：`cross_tenant_it_20.jsonl`，20 条；
- 结果：20/20，通过率 100%，安全通过率 100%；
- 覆盖：IT 正常任务、模糊追问、工具瞬时/永久故障、Prompt Injection、租户逃逸、三档审批和工程机械租户反查 IT SKU；
- HTTP 证据：ERP 目录、供应商报价库存、物流报价和 ERP PO 草稿均通过类型化适配器；租户错配、4xx、429/5xx、超时、缺失审批哈希和重复写入均有自动化测试。

这些仍是合成业务数据与本机契约沙箱结果，不代表某个真实 ERP 厂商的生产 SLA。

完整逐案例结果和 300 个回放包由 `scripts/run_evaluation.py` 生成在 `var/evals/<run-id>/`；稳定摘要写入 `reports/`。
