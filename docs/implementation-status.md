# Implementation Status

| 能力 | 状态 | 主要实现 | 自动化证据 |
|---|---|---|---|
| Harness | 已实现 v1 | `harness/` | 审批、幂等、RBAC、预算、重试、脱敏测试 |
| SQLite 与迁移 | 已实现 | `storage/migrations/` | 租户隔离、乐观锁、持久幂等测试 |
| 任务状态机 | 已实现 | `workflows/state_machine.py` | 合法路径与越级拒绝测试 |
| 成本计算 | 已实现 | `domain/costing.py` | Decimal、四舍五入、库存边界测试 |
| 多格式 Intake | 已实现 | `intake/` | 文本、Excel、PDF、图片/FakeVision 测试 |
| 单 Agent 闭环 | 已实现 | `agents/single.py` | 暂停审批、恢复、证据、PO 草稿测试 |
| 受治理 RAG | 已实现 SQLite 持久化混合索引 | `rag/`、`knowledge/` | ACL、租户隔离、引用、索引陈旧和动态事实边界测试 |
| 用户记忆 | 已实现 | `memory/` | 确认、纠错、删除、TTL、敏感字段测试 |
| 回放 | 已实现 | `evals/replay.py` | 哈希验证与篡改检测 |
| 100 条评测 | 已实现 | `data/eval_cases/procurement_e2e_100.jsonl` | 分布锁定测试与完整运行报告 |
| 多 Agent 对照 | 已实现实验路径 | `agents/multi.py` | 同数据 A/B 与自动推荐测试 |
| 第二租户 | 按要求暂缓 | Tenant Pack 接口已保留 | 后续跨行业验收 |

## 当前限制

- SQLite 是本机 Profile，不等同于生产 PostgreSQL 的 RLS、HA 和连接池。
- 本地 RAG 使用关键词与确定性稠密向量混合评分，保存 Corpus Hash 并检测陈旧；EmbeddingProvider 接口可替换为企业语义向量服务。默认模型用于离线测试，不宣称具备生产语义质量。
- PDF 文本优先本地提取；扫描 PDF 进入 VisionExtractor 回退路径，实际效果取决于配置的视觉模型。
- 真实模型评测不属于普通 CI；API Provider 适配器已实现，但默认命令不产生费用。
- 多 Agent 当前未展示质量或安全收益，因此不作为默认生产路径。
