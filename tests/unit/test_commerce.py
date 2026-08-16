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


def test_commerce_intent_prefers_dimensions_before_generic_gmv(tmp_path):
    store = CommerceAnalyticsStore(tmp_path / "commerce.sqlite3")
    assert store.classify("哪个区域的销售额最高？") == "region_sales"
    assert store.classify("销售额最高的商品是哪一个？") == "product_sales"
    assert store.classify("每天的销售额趋势") == "gmv"
    store.close()
