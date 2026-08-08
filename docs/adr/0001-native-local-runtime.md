# ADR-0001: Native Local Runtime

## Status

Accepted

## Decision

开发与演示阶段不使用 Docker 或 WSL2。使用 Python 3.12、SQLite 和本地文件存储；通过 Repository、AuditSink 和 Gateway 接口隔离基础设施。

## Consequences

- 优点：启动简单，适合个人开发和面试现场演示。
- 风险：SQLite 不提供 PostgreSQL RLS 和跨进程并发能力。
- 缓解：跨租户测试仍为强制门禁；未来增加 PostgreSQL Profile，不改变领域契约。

