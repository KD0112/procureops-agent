# ProcureOps Engineering Contract

本文件适用于整个仓库。任何人工或编码 Agent 的修改都必须遵守以下约束。

## 不可变约束

1. 不读取或修改 `D:\new things\项目1\day1`，唯一例外是运行时只读加载用户已指定的 `.env`。
2. 业务模块不得直接调用模型 Provider SDK、HTTP API 或数据库连接；统一经过 Harness 网关和类型化适配器。
3. 价格、库存、物流时效和订单状态只能来自数据库或工具，禁止进入 RAG 文档。
4. 缺少租户、角色、证据、审批、Schema 或版本信息时必须 fail closed。
5. 所有写工具必须声明幂等策略；所有高风险工具必须校验审批绑定哈希。
6. 日志和测试输出不得包含 API Key、Token、密码、完整敏感载荷或未脱敏个人信息。
7. 生产反馈不得自动改写 Prompt、规则、评测集或代码；只能形成候选，经离线评测和人工发布。

## 修改顺序

1. 先更新或确认领域契约、ADR、规则版本。
2. 先写失败测试，覆盖正常路径和至少一个安全/故障路径。
3. 实现最小改动，不绕过 Harness。
4. 更新回放字段、审计事件和面试问题映射（如适用）。
5. 运行 `scripts/verify.ps1`，不允许以跳过测试的方式获得绿灯。

## Definition of Done

- FakeModel/模拟工具测试通过，不依赖付费 API。
- Ruff 与 Pytest 全部通过。
- 新风险动作已进入审批矩阵。
- 新 RAG 文档含完整治理元数据，且 `knowledge/manifest.json` 已重建。
- 动态事实带 `observed_at`，需要时带 `valid_until`。
- 任何 Schema、Prompt、规则、Tenant Pack 或工作流变化都有显式版本。
