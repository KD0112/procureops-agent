# Governed Evolution, Memory and Model Routing

## 1. “自主进化”的企业定义

ProcureOps 不允许生产 Agent 自动重写 Prompt、规则或代码。这里的持续进化是一个受治理发布流程：

```text
用户反馈
  -> Prompt 候选（必须关联反馈和基线版本）
  -> 同一 Gold Set 的基线/候选 Harness FakeModel 回归
  -> compliance_approver 审批
  -> 人工发布新版本
  -> 新任务读取活动版本
  -> 指标异常时回滚前一版本
```

数据库保存反馈哈希、候选 Prompt 哈希、评测报告、提议人、审批人、发布时间和版本链。未发布候选不会影响生产任务；发布动作不能由采购员角色单独完成。

## 2. 用户记忆

当前只识别明确表达且在白名单内的非敏感偏好，例如交付时间、供应商比较策略和是否接受等效件。每条记录带完整性哈希，值还要经过指令注入和大小限制检查。生命周期为：

```text
显式偏好 -> candidate -> 用户确认 -> confirmed -> 纠错/删除/TTL 过期
```

隔离键为 `tenant_id + user_id`。API Key、Token、密码、银行卡、身份证和审批阈值等字段会被拒绝。确认记忆被使用时，任务会新增 `confirmed_memory` 字段证据；记忆不能覆盖采购政策。

## 3. 千问接入

适配器使用 DashScope 的 OpenAI-compatible endpoint，同一 Harness 支持文本和视觉：

- 文本默认示例模型：`qwen-flash`
- 视觉默认示例模型：`qwen-vl-plus`
- 密钥：`DASHSCOPE_API_KEY`，兼容 `QWEN_API_KEY`
- Base URL：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 结构化视觉请求设置 `enable_thinking=false` 并要求 JSON object

配置 `AGENT_TEXT_ROUTE=qwen,deepseek` 和 `AGENT_VISION_ROUTE=qwen,zhipu` 后，Qwen 是首选，其他 Provider 是故障回退。路由层对每个真实提供方尝试分别计预算和审计，并带连续失败熔断；业务模块不感知 Provider。

本机当前没有 DashScope 密钥，因此项目只完成了适配器、FakeTransport 测试和相同评测命令入口，没有伪造真实千问通过报告。配置密钥后运行：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_live_model_eval.py --provider qwen --limit 20
& ".\.venv\Scripts\python.exe" scripts\run_live_vision_smoke.py --provider qwen
```

## 4. 面试时应主动说明

1. 千问“免费”通常是账号或模型的限时/限量额度，不等于永久无条件免费，仍需 API Key 并监控额度。
2. FakeModel 的 100 条评测证明流程、边界和可回放性，不代表真实模型业务准确率。
3. 当前三路对照没有显示多 Agent 质量收益，所以默认单 Agent；这体现按证据选架构，而不是为展示堆 Agent。
4. SQLite 是本机 Profile；生产版会换 PostgreSQL/RLS 和分布式 Worker，但领域契约与 Harness 不变。
