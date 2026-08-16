from __future__ import annotations

import json

from procureops.commerce import CommerceAnalyticsStore


def test_commerce_insight_uses_allowlisted_sql(tmp_path):
    seed = tmp_path / "analytics.json"
    seed.write_text(
        json.dumps(
            {
                "tenant_id": "tenant_commerce_ops",
                "products": [
                    {"product_id": "p1", "name": "Keyboard", "category": "digital"}
                ],
                "orders": [
                    {
                        "order_id": "o1",
                        "product_id": "p1",
                        "region": "East",
                        "order_date": "2026-07-01",
                        "quantity": 2,
                        "unit_price": 10,
                        "returned": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = CommerceAnalyticsStore(tmp_path / "commerce.sqlite3", seed_path=seed)
    insight = store.insight(tenant_id="tenant_commerce_ops", query="哪个商品退货率最高？")
    assert insight.intent == "return_rate"
    assert insight.rows[0]["return_rate_pct"] == 100.0
    assert "SELECT" in insight.sql_template
    store.close()
