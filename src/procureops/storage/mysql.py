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

    async def seed_commerce_demo(self, *, seed_path: Path, tenant_id: str) -> None:
        """Seed the CommerceOps analytics slice with one idempotent transaction."""

        from sqlalchemy import text

        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        if payload.get("tenant_id") != tenant_id:
            raise ValueError("commerce seed tenant_id does not match the requested tenant")
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO tenants (tenant_id, display_name)
                    VALUES (:tenant_id, :display_name)
                    ON DUPLICATE KEY UPDATE display_name = :display_name
                    """
                ),
                {"tenant_id": tenant_id, "display_name": "电商运营分析演示租户"},
            )
            for product in payload.get("products", []):
                await session.execute(
                    text(
                        """
                        INSERT INTO commerce_products (tenant_id, product_id, name, category)
                        VALUES (:tenant_id, :product_id, :name, :category)
                        ON DUPLICATE KEY UPDATE name = :name, category = :category
                        """
                    ),
                    {"tenant_id": tenant_id, **product},
                )
            for order in payload.get("orders", []):
                await session.execute(
                    text(
                        """
                        INSERT INTO commerce_orders
                            (tenant_id, order_id, product_id, region, order_date,
                             quantity, unit_price, returned_flag, return_reason)
                        VALUES (:tenant_id, :order_id, :product_id, :region, :order_date,
                                :quantity, :unit_price, :returned_flag, :return_reason)
                        ON DUPLICATE KEY UPDATE
                            product_id = :product_id, region = :region,
                            order_date = :order_date, quantity = :quantity,
                            unit_price = :unit_price, returned_flag = :returned_flag,
                            return_reason = :return_reason
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        **order,
                        "returned_flag": bool(order.get("returned", False)),
                        "return_reason": order.get("return_reason"),
                    },
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

    async def commerce_insight(
        self, *, tenant_id: str, intent: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Run one of the allowlisted CommerceOps read-only SQL queries."""

        from sqlalchemy import text

        queries = {
            "summary": """
                SELECT COUNT(*) AS order_count,
                       ROUND(SUM(quantity * unit_price), 2) AS gmv,
                       SUM(returned_flag) AS returned_orders,
                       ROUND(100.0 * SUM(returned_flag) / NULLIF(COUNT(*), 0), 2)
                           AS return_rate_pct
                FROM commerce_orders WHERE tenant_id = :tenant_id
            """,
            "return_rate": """
                SELECT p.product_id, p.name, COUNT(o.order_id) AS order_count,
                       SUM(o.returned_flag) AS returned_orders,
                       ROUND(100.0 * SUM(o.returned_flag) / NULLIF(COUNT(o.order_id), 0), 2)
                           AS return_rate_pct
                FROM commerce_orders o
                JOIN commerce_products p
                  ON p.tenant_id=o.tenant_id AND p.product_id=o.product_id
                WHERE o.tenant_id = :tenant_id
                GROUP BY p.product_id, p.name
                ORDER BY return_rate_pct DESC, order_count DESC LIMIT :limit
            """,
            "region_sales": """
                SELECT region, COUNT(*) AS order_count,
                       ROUND(SUM(quantity * unit_price), 2) AS gmv
                FROM commerce_orders WHERE tenant_id = :tenant_id
                GROUP BY region ORDER BY gmv DESC LIMIT :limit
            """,
            "product_sales": """
                SELECT p.product_id, p.name, p.category,
                       SUM(o.quantity) AS units,
                       ROUND(SUM(o.quantity * o.unit_price), 2) AS gmv
                FROM commerce_orders o
                JOIN commerce_products p
                  ON p.tenant_id=o.tenant_id AND p.product_id=o.product_id
                WHERE o.tenant_id = :tenant_id
                GROUP BY p.product_id, p.name, p.category
                ORDER BY gmv DESC LIMIT :limit
            """,
        }
        selected = queries.get(intent, queries["summary"])
        async with self.sessions() as session:
            result = await session.execute(
                text(selected), {"tenant_id": tenant_id, "limit": limit}
            )
            return [dict(row) for row in result.mappings().all()]

    async def close(self) -> None:
        await self.engine.dispose()
