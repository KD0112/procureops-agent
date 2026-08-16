"""Allowlisted SQL analytics for the CommerceOps tenant.

The user query selects a metric through a small intent map; it never becomes
SQL. This makes the demo useful for explaining the SQL/RAG boundary and keeps
the analytics tool read-only and auditable.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class CommerceInsight:
    tenant_id: str
    intent: str
    sql_template: str
    rows: tuple[dict[str, Any], ...]
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "intent": self.intent,
            "sql_template": self.sql_template,
            "rows": [dict(row) for row in self.rows],
            "source": self.source,
        }


class CommerceAnalyticsStore:
    """Small local SQL mirror used by the offline and Docker demo profiles."""

    def __init__(self, path: Path, *, seed_path: Path | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._create_schema()
        if seed_path is not None:
            self.seed(seed_path)

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS commerce_products (
                tenant_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                PRIMARY KEY (tenant_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS commerce_orders (
                tenant_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                region TEXT NOT NULL,
                order_date TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price REAL NOT NULL CHECK (unit_price >= 0),
                returned INTEGER NOT NULL DEFAULT 0 CHECK (returned IN (0, 1)),
                return_reason TEXT,
                PRIMARY KEY (tenant_id, order_id),
                FOREIGN KEY (tenant_id, product_id)
                    REFERENCES commerce_products (tenant_id, product_id)
            );
            CREATE INDEX IF NOT EXISTS idx_commerce_orders_tenant_date
                ON commerce_orders (tenant_id, order_date);
            CREATE INDEX IF NOT EXISTS idx_commerce_orders_tenant_product
                ON commerce_orders (tenant_id, product_id);
            """
        )
        self.connection.commit()

    def seed(self, seed_path: Path) -> None:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        tenant_id = str(payload["tenant_id"])
        with self._lock, self.connection:
            for product in payload.get("products", []):
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO commerce_products
                        (tenant_id, product_id, name, category)
                    VALUES (?, ?, ?, ?)
                    """,
                    (tenant_id, product["product_id"], product["name"], product["category"]),
                )
            for order in payload.get("orders", []):
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO commerce_orders
                        (tenant_id, order_id, product_id, region, order_date,
                         quantity, unit_price, returned, return_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        order["order_id"],
                        order["product_id"],
                        order["region"],
                        order["order_date"],
                        int(order["quantity"]),
                        float(order["unit_price"]),
                        int(bool(order.get("returned", False))),
                        order.get("return_reason"),
                    ),
                )

    @staticmethod
    def classify(query: str) -> str:
        text = query.casefold()
        if any(token in text for token in ("退货率", "退货", "return")):
            return "return_rate"
        if any(token in text for token in ("销售额", "gmv", "营收", "成交")):
            return "gmv"
        if any(token in text for token in ("区域", "地区", "地域")):
            return "region_sales"
        if any(token in text for token in ("商品", "sku", "产品")):
            return "product_sales"
        return "summary"

    def insight(self, *, tenant_id: str, query: str, limit: int = 10) -> CommerceInsight:
        intent = self.classify(query)
        queries: dict[str, tuple[str, str]] = {
            "summary": (
                "summary",
                """
                SELECT COUNT(*) AS order_count,
                       ROUND(SUM(quantity * unit_price), 2) AS gmv,
                       SUM(returned) AS returned_orders,
                       ROUND(100.0 * SUM(returned) / NULLIF(COUNT(*), 0), 2) AS return_rate_pct
                FROM commerce_orders
                WHERE tenant_id = :tenant_id
                """,
            ),
            "gmv": (
                "gmv",
                """
                SELECT order_date, ROUND(SUM(quantity * unit_price), 2) AS gmv
                FROM commerce_orders
                WHERE tenant_id = :tenant_id
                GROUP BY order_date ORDER BY order_date
                LIMIT :limit
                """,
            ),
            "return_rate": (
                "return_rate",
                """
                SELECT p.product_id, p.name,
                       COUNT(o.order_id) AS order_count,
                       SUM(o.returned) AS returned_orders,
                       ROUND(100.0 * SUM(o.returned) / NULLIF(COUNT(o.order_id), 0), 2)
                           AS return_rate_pct
                FROM commerce_orders o
                JOIN commerce_products p
                  ON p.tenant_id = o.tenant_id AND p.product_id = o.product_id
                WHERE o.tenant_id = :tenant_id
                GROUP BY p.product_id, p.name
                ORDER BY return_rate_pct DESC, order_count DESC
                LIMIT :limit
                """,
            ),
            "region_sales": (
                "region_sales",
                """
                SELECT region, COUNT(*) AS order_count,
                       ROUND(SUM(quantity * unit_price), 2) AS gmv
                FROM commerce_orders
                WHERE tenant_id = :tenant_id
                GROUP BY region ORDER BY gmv DESC
                LIMIT :limit
                """,
            ),
            "product_sales": (
                "product_sales",
                """
                SELECT p.product_id, p.name, p.category,
                       SUM(o.quantity) AS units,
                       ROUND(SUM(o.quantity * o.unit_price), 2) AS gmv
                FROM commerce_orders o
                JOIN commerce_products p
                  ON p.tenant_id = o.tenant_id AND p.product_id = o.product_id
                WHERE o.tenant_id = :tenant_id
                GROUP BY p.product_id, p.name, p.category
                ORDER BY gmv DESC
                LIMIT :limit
                """,
            ),
        }
        _, sql = queries[intent]
        with self._lock:
            rows = tuple(
                dict(row)
                for row in self.connection.execute(
                    sql, {"tenant_id": tenant_id, "limit": limit}
                ).fetchall()
            )
        return CommerceInsight(
            tenant_id=tenant_id,
            intent=intent,
            sql_template=" ".join(sql.split()),
            rows=rows,
            source="sqlite_sql_mirror",
        )

    def close(self) -> None:
        with self._lock:
            self.connection.close()
