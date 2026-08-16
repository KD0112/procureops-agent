from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class MySQLSettings:
    url: str
    pool_size: int = 5
    max_overflow: int = 10

    @classmethod
    def from_environment(cls) -> MySQLSettings | None:
        url = os.getenv("PROCUREOPS_MYSQL_URL", "").strip()
        return cls(url) if url else None


class MySQLBusinessRepository:
    """Async MySQL vertical slice; SQLite remains the offline default.

    This adapter intentionally covers the business path needed for the
    enterprise demo instead of pretending that a raw SQLite repository can be
    switched to MySQL by changing a connection string.
    """

    def __init__(self, settings: MySQLSettings, *, schema_path: Path | None = None) -> None:
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        except ImportError as exc:
            raise RuntimeError("install the [infra] extra for MySQL support") from exc
        self.settings = settings
        self.schema_path = schema_path or Path(__file__).with_name("mysql_schema.sql")
        self.engine = create_async_engine(
            settings.url,
            pool_pre_ping=True,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        from sqlalchemy import text

        statements = [
            item.strip()
            for item in self.schema_path.read_text(encoding="utf-8").split(";")
            if item.strip()
        ]
        async with self.engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))

    async def health(self) -> dict[str, Any]:
        from sqlalchemy import text

        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ok", "backend": "mysql"}

    async def seed_demo_catalog(self, *, tenant_id: str = "demo-tenant") -> None:
        """Create a small idempotent catalog for local integration smoke tests."""

        from sqlalchemy import text

        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO tenants (tenant_id, display_name)
                    VALUES (:tenant_id, 'Demo Procurement Tenant')
                    AS new
                    ON DUPLICATE KEY UPDATE display_name = new.display_name
                    """
                ),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO products (tenant_id, product_id, name, part_number, status)
                    VALUES (:tenant_id, 'demo-product', 'Demo Bearing', 'DB-001', 'active')
                    AS new
                    ON DUPLICATE KEY UPDATE name = new.name, status = new.status
                    """
                ),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO suppliers (tenant_id, supplier_id, name, approved)
                    VALUES (:tenant_id, 'demo-supplier', 'Demo Supplier', 1)
                    AS new
                    ON DUPLICATE KEY UPDATE name = new.name, approved = new.approved
                    """
                ),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO inventory (tenant_id, product_id, available_quantity)
                    VALUES (:tenant_id, 'demo-product', 42)
                    AS new
                    ON DUPLICATE KEY UPDATE available_quantity = new.available_quantity
                    """
                ),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO quotations
                        (tenant_id, quotation_id, product_id, supplier_id, unit_price, currency)
                    VALUES (
                        :tenant_id, 'demo-quotation', 'demo-product', 'demo-supplier', 12.5, 'CNY'
                    )
                    AS new
                    ON DUPLICATE KEY UPDATE
                        unit_price = new.unit_price, currency = new.currency
                    """
                ),
                {"tenant_id": tenant_id},
            )

    async def create_task_with_outbox(
        self,
        *,
        tenant_id: str,
        task_id: str,
        actor_id: str,
        request: dict[str, Any],
    ) -> None:
        from sqlalchemy import text

        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO procurement_tasks
                        (tenant_id, task_id, created_by, status, request_json)
                    VALUES (:tenant_id, :task_id, :created_by, 'pending', :request_json)
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "task_id": task_id,
                    "created_by": actor_id,
                    "request_json": json.dumps(request, ensure_ascii=False),
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO outbox_events
                        (tenant_id, task_id, event_type, payload_json)
                    VALUES (:tenant_id, :task_id, 'task.created', :payload_json)
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "task_id": task_id,
                    "payload_json": json.dumps(
                        {"tenant_id": tenant_id, "task_id": task_id}, ensure_ascii=False
                    ),
                },
            )

    async def search_catalog(
        self, *, tenant_id: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        async with self.sessions() as session:
            result = await session.execute(
                text(
                    """
                    SELECT p.product_id, p.name, p.part_number,
                           COALESCE(i.available_quantity, 0) AS available_quantity,
                           q.quotation_id, q.unit_price, q.currency,
                           s.supplier_id, s.name AS supplier_name
                    FROM products p
                    LEFT JOIN inventory i
                      ON i.tenant_id = p.tenant_id AND i.product_id = p.product_id
                    LEFT JOIN quotations q
                      ON q.tenant_id = p.tenant_id AND q.product_id = p.product_id
                    LEFT JOIN suppliers s
                      ON s.tenant_id = q.tenant_id AND s.supplier_id = q.supplier_id
                    WHERE p.tenant_id = :tenant_id
                      AND p.status = 'active'
                      AND (p.name LIKE :query OR p.part_number LIKE :query)
                    ORDER BY p.name, q.unit_price
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "query": f"%{query}%", "limit": limit},
            )
            return [dict(row) for row in result.mappings().all()]

    async def close(self) -> None:
        await self.engine.dispose()
