from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from procureops.domain.enums import TaskStatus
from procureops.domain.models import ApprovalGrant, canonical_hash
from procureops.domain.procurement import (
    LogisticsQuote,
    ProcurementLine,
    ProductCandidate,
    SupplierOption,
    TaskSnapshot,
)
from procureops.harness.errors import IdempotencyConflict
from procureops.storage.database import SQLiteDatabase
from procureops.workflows.state_machine import ProcurementStateMachine


def utc_now() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class ProcureOpsRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def seed_tenant(
        self,
        *,
        tenant: dict[str, Any],
        products: list[dict[str, Any]],
        suppliers: list[dict[str, Any]],
        quotations: list[dict[str, Any]],
        inventory: list[dict[str, Any]],
        logistics: list[dict[str, Any]] | None = None,
    ) -> None:
        created_at = utc_now().isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tenants(tenant_id, display_name, tenant_pack_version, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    tenant_pack_version=excluded.tenant_pack_version
                """,
                (
                    tenant["tenant_id"],
                    tenant["display_name"],
                    tenant["tenant_pack_version"],
                    created_at,
                ),
            )
            for product in products:
                connection.execute(
                    """
                    INSERT INTO products(
                        product_id, tenant_id, sku, name, category, aliases_json,
                        compatibility_tags_json, unit, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(tenant_id, product_id) DO UPDATE SET
                        sku=excluded.sku, name=excluded.name, category=excluded.category,
                        aliases_json=excluded.aliases_json,
                        compatibility_tags_json=excluded.compatibility_tags_json,
                        unit=excluded.unit, active=1
                    """,
                    (
                        product["product_id"],
                        tenant["tenant_id"],
                        product["sku"],
                        product["name"],
                        product["category"],
                        _json(product.get("aliases", [])),
                        _json(product.get("compatibility_tags", [])),
                        product["unit"],
                    ),
                )
            for supplier in suppliers:
                connection.execute(
                    """
                    INSERT INTO suppliers(
                        supplier_id, tenant_id, name, approved, risk_level
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, supplier_id) DO UPDATE SET
                        name=excluded.name, approved=excluded.approved,
                        risk_level=excluded.risk_level
                    """,
                    (
                        supplier["supplier_id"],
                        tenant["tenant_id"],
                        supplier["name"],
                        int(supplier["approved"]),
                        supplier["risk_level"],
                    ),
                )
            for quotation in quotations:
                connection.execute(
                    """
                    INSERT INTO quotations(
                        quotation_id, tenant_id, supplier_id, product_id,
                        unit_price, currency, tax_rate, freight, observed_at, valid_until
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, quotation_id) DO UPDATE SET
                        unit_price=excluded.unit_price, tax_rate=excluded.tax_rate,
                        freight=excluded.freight, observed_at=excluded.observed_at,
                        valid_until=excluded.valid_until
                    """,
                    (
                        quotation["quotation_id"],
                        tenant["tenant_id"],
                        quotation["supplier_id"],
                        quotation["product_id"],
                        str(quotation["unit_price"]),
                        quotation["currency"],
                        str(quotation["tax_rate"]),
                        str(quotation["freight"]),
                        quotation["observed_at"],
                        quotation["valid_until"],
                    ),
                )
            for snapshot in inventory:
                connection.execute(
                    """
                    INSERT INTO inventory(
                        tenant_id, supplier_id, product_id, quantity,
                        observed_at, valid_until
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, supplier_id, product_id) DO UPDATE SET
                        quantity=excluded.quantity, observed_at=excluded.observed_at,
                        valid_until=excluded.valid_until
                    """,
                    (
                        tenant["tenant_id"],
                        snapshot["supplier_id"],
                        snapshot["product_id"],
                        str(snapshot["quantity"]),
                        snapshot["observed_at"],
                        snapshot["valid_until"],
                    ),
                )
            for quote in logistics or []:
                connection.execute(
                    """
                    INSERT INTO logistics_quotes(
                        tenant_id, logistics_quote_id, supplier_id, product_id,
                        shipping_method, lead_time_days, shipping_cost,
                        observed_at, valid_until
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, logistics_quote_id) DO UPDATE SET
                        shipping_method=excluded.shipping_method,
                        lead_time_days=excluded.lead_time_days,
                        shipping_cost=excluded.shipping_cost,
                        observed_at=excluded.observed_at,
                        valid_until=excluded.valid_until
                    """,
                    (
                        tenant["tenant_id"],
                        quote["logistics_quote_id"],
                        quote["supplier_id"],
                        quote["product_id"],
                        quote["shipping_method"],
                        quote["lead_time_days"],
                        quote["shipping_cost"],
                        quote["observed_at"],
                        quote["valid_until"],
                    ),
                )

    def create_task(
        self,
        *,
        tenant_id: str,
        created_by: str,
        request: dict[str, Any],
        workflow_version: str,
        task_id: str | None = None,
    ) -> TaskSnapshot:
        task_id = task_id or str(uuid4())
        now = utc_now().isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO procurement_tasks(
                    task_id, tenant_id, created_by, status, request_json,
                    workflow_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    tenant_id,
                    created_by,
                    TaskStatus.DRAFT,
                    _json(request),
                    workflow_version,
                    now,
                    now,
                ),
            )
        return self.get_task(tenant_id=tenant_id, task_id=task_id)

    def create_task_with_outbox(
        self,
        *,
        tenant_id: str,
        created_by: str,
        request: dict[str, Any],
        workflow_version: str,
        task_id: str,
        job_payload: dict[str, Any],
        idempotency_key: str,
        upload: dict[str, Any] | None = None,
    ) -> tuple[TaskSnapshot, str, str | None]:
        """Atomically persist the task, optional upload metadata, and queue intent."""

        now = utc_now().isoformat()
        outbox_event_id = str(uuid4())
        upload_id = str(uuid4()) if upload is not None else None
        outbox_payload = {
            "job_type": "process_intake",
            "job_payload": job_payload,
            "max_attempts": 3,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO procurement_tasks(
                    task_id, tenant_id, created_by, status, request_json,
                    workflow_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    tenant_id,
                    created_by,
                    TaskStatus.DRAFT,
                    _json(request),
                    workflow_version,
                    now,
                    now,
                ),
            )
            if upload is not None and upload_id is not None:
                connection.execute(
                    """
                    INSERT INTO task_uploads(
                        upload_id, tenant_id, task_id, original_filename,
                        storage_key, content_type, size_bytes, sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        upload_id,
                        tenant_id,
                        task_id,
                        upload["original_filename"],
                        upload["storage_key"],
                        upload["content_type"],
                        upload["size_bytes"],
                        upload["sha256"],
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO outbox_events(
                    event_id, tenant_id, aggregate_type, aggregate_id,
                    event_type, payload_json, payload_hash, idempotency_key,
                    status, created_at
                ) VALUES (?, ?, 'procurement_task', ?, 'work.requested',
                          ?, ?, ?, 'pending', ?)
                """,
                (
                    outbox_event_id,
                    tenant_id,
                    task_id,
                    _json(outbox_payload),
                    canonical_hash(outbox_payload),
                    idempotency_key,
                    now,
                ),
            )
        return (
            self.get_task(tenant_id=tenant_id, task_id=task_id),
            outbox_event_id,
            upload_id,
        )

    def task_created_by(self, *, tenant_id: str, task_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT created_by FROM procurement_tasks WHERE tenant_id=? AND task_id=?",
                (tenant_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError("task not found in tenant scope")
        return str(row["created_by"])

    def list_tasks(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT task_id, tenant_id, created_by, status, request_json,
                       workflow_version, version, created_at, updated_at
                FROM procurement_tasks
                WHERE tenant_id=? ORDER BY updated_at DESC LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()
        return tuple(
            {**dict(row), "request": json.loads(row["request_json"])} for row in rows
        )

    def add_upload(
        self,
        *,
        tenant_id: str,
        task_id: str,
        original_filename: str,
        storage_key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> str:
        upload_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO task_uploads(
                    upload_id, tenant_id, task_id, original_filename,
                    storage_key, content_type, size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_id,
                    tenant_id,
                    task_id,
                    original_filename,
                    storage_key,
                    content_type,
                    size_bytes,
                    sha256,
                    utc_now().isoformat(),
                ),
            )
        return upload_id

    def uploads_for_task(
        self,
        *,
        tenant_id: str,
        task_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_uploads
                WHERE tenant_id=? AND task_id=? ORDER BY created_at
                """,
                (tenant_id, task_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_task(self, *, tenant_id: str, task_id: str) -> TaskSnapshot:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, task_id, status, version, request_json
                FROM procurement_tasks WHERE tenant_id=? AND task_id=?
                """,
                (tenant_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"task not found for tenant: {task_id}")
        return TaskSnapshot(
            tenant_id=row["tenant_id"],
            task_id=row["task_id"],
            status=row["status"],
            version=row["version"],
            request=json.loads(row["request_json"]),
        )

    def transition_task(
        self,
        *,
        tenant_id: str,
        task_id: str,
        target: TaskStatus,
        expected_version: int,
    ) -> TaskSnapshot:
        current = self.get_task(tenant_id=tenant_id, task_id=task_id)
        ProcurementStateMachine.ensure_allowed(TaskStatus(current.status), target)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE procurement_tasks
                SET status=?, version=version+1, updated_at=?
                WHERE tenant_id=? AND task_id=? AND version=?
                """,
                (
                    target,
                    utc_now().isoformat(),
                    tenant_id,
                    task_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("task version conflict")
        return self.get_task(tenant_id=tenant_id, task_id=task_id)

    def replace_task_items(
        self,
        *,
        tenant_id: str,
        task_id: str,
        lines: list[ProcurementLine],
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM task_items WHERE tenant_id=? AND task_id=?",
                (tenant_id, task_id),
            )
            for line in lines:
                connection.execute(
                    """
                    INSERT INTO task_items(
                        item_id, tenant_id, task_id, line_number, description,
                        quantity, unit, requested_part_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        tenant_id,
                        task_id,
                        line.line_number,
                        line.description,
                        str(line.quantity),
                        line.unit,
                        line.part_number,
                    ),
                )

    def task_items(self, *, tenant_id: str, task_id: str) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_items
                WHERE tenant_id=? AND task_id=? ORDER BY line_number
                """,
                (tenant_id, task_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def search_products(
        self,
        *,
        tenant_id: str,
        query: str,
        part_number: str | None = None,
        limit: int = 5,
    ) -> tuple[ProductCandidate, ...]:
        normalized_query = query.casefold().strip()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM products WHERE tenant_id=? AND active=1",
                (tenant_id,),
            ).fetchall()
        candidates: list[ProductCandidate] = []
        for row in rows:
            aliases = [str(item) for item in json.loads(row["aliases_json"])]
            reasons: list[str] = []
            score = Decimal("0")
            if part_number and part_number.casefold() == row["sku"].casefold():
                score = Decimal("1")
                reasons.append("exact_sku")
            elif normalized_query == row["name"].casefold():
                score = Decimal("0.95")
                reasons.append("exact_name")
            elif normalized_query in {alias.casefold() for alias in aliases}:
                score = Decimal("0.90")
                reasons.append("exact_alias")
            else:
                searchable = " ".join([row["sku"], row["name"], *aliases]).casefold()
                query_tokens = {item for item in normalized_query.split() if item}
                matched = {item for item in query_tokens if item in searchable}
                if matched:
                    score = Decimal(str(0.55 + 0.3 * len(matched) / len(query_tokens)))
                    reasons.append("token_overlap")
                elif normalized_query and normalized_query in searchable:
                    score = Decimal("0.60")
                    reasons.append("substring")
            if score > 0:
                candidates.append(
                    ProductCandidate(
                        product_id=row["product_id"],
                        sku=row["sku"],
                        name=row["name"],
                        category=row["category"],
                        unit=row["unit"],
                        score=score,
                        match_reasons=tuple(reasons),
                    )
                )
        candidates.sort(key=lambda item: (-item.score, item.sku))
        return tuple(candidates[:limit])

    def select_product(
        self,
        *,
        tenant_id: str,
        task_id: str,
        line_number: int,
        candidate: ProductCandidate,
    ) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE task_items SET matched_product_id=?, match_confidence=?
                WHERE tenant_id=? AND task_id=? AND line_number=?
                """,
                (
                    candidate.product_id,
                    str(candidate.score),
                    tenant_id,
                    task_id,
                    line_number,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("task item not found")

    def supplier_options(
        self,
        *,
        tenant_id: str,
        product_id: str,
        required_quantity: Decimal,
        at: datetime | None = None,
    ) -> tuple[SupplierOption, ...]:
        observed_at = (at or utc_now()).isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.supplier_id, s.name supplier_name, s.approved, s.risk_level,
                       q.quotation_id, q.product_id, q.unit_price, q.currency,
                       q.tax_rate, q.freight, q.observed_at quote_observed_at,
                       q.valid_until quote_valid_until, i.quantity available_quantity,
                       i.observed_at inventory_observed_at,
                       i.valid_until inventory_valid_until
                FROM quotations q
                JOIN suppliers s ON s.tenant_id=q.tenant_id
                    AND s.supplier_id=q.supplier_id
                JOIN inventory i ON i.tenant_id=q.tenant_id
                    AND i.supplier_id=q.supplier_id AND i.product_id=q.product_id
                WHERE q.tenant_id=? AND q.product_id=?
                  AND q.valid_until>=? AND i.valid_until>=?
                """,
                (tenant_id, product_id, observed_at, observed_at),
            ).fetchall()
        options = [
            SupplierOption(
                supplier_id=row["supplier_id"],
                supplier_name=row["supplier_name"],
                approved=bool(row["approved"]),
                risk_level=row["risk_level"],
                quotation_id=row["quotation_id"],
                product_id=row["product_id"],
                unit_price=Decimal(row["unit_price"]),
                currency=row["currency"],
                tax_rate=Decimal(row["tax_rate"]),
                freight=Decimal(row["freight"]),
                available_quantity=Decimal(row["available_quantity"]),
                observed_at=datetime.fromisoformat(row["quote_observed_at"]),
                valid_until=min(
                    datetime.fromisoformat(row["quote_valid_until"]),
                    datetime.fromisoformat(row["inventory_valid_until"]),
                ),
            )
            for row in rows
            if Decimal(row["available_quantity"]) >= required_quantity
        ]
        options.sort(
            key=lambda item: (
                not item.approved,
                item.unit_price * required_quantity + item.freight,
                item.supplier_id,
            )
        )
        return tuple(options)

    def select_supplier(
        self,
        *,
        tenant_id: str,
        task_id: str,
        line_number: int,
        option: SupplierOption,
    ) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE task_items
                SET selected_supplier_id=?, selected_quotation_id=?
                WHERE tenant_id=? AND task_id=? AND line_number=?
                """,
                (
                    option.supplier_id,
                    option.quotation_id,
                    tenant_id,
                    task_id,
                    line_number,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("task item not found")

    def logistics_quotes(
        self,
        *,
        tenant_id: str,
        product_id: str,
        supplier_ids: tuple[str, ...],
        at: datetime | None = None,
    ) -> tuple[LogisticsQuote, ...]:
        if not supplier_ids:
            return ()
        observed_at = (at or utc_now()).isoformat()
        placeholders = ",".join("?" for _ in supplier_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM logistics_quotes
                WHERE tenant_id=? AND product_id=? AND valid_until>=?
                  AND supplier_id IN ({placeholders})
                ORDER BY lead_time_days, shipping_cost, supplier_id
                """,
                (tenant_id, product_id, observed_at, *supplier_ids),
            ).fetchall()
        return tuple(
            LogisticsQuote(
                logistics_quote_id=row["logistics_quote_id"],
                supplier_id=row["supplier_id"],
                product_id=row["product_id"],
                shipping_method=row["shipping_method"],
                lead_time_days=row["lead_time_days"],
                shipping_cost=Decimal(row["shipping_cost"]),
                observed_at=datetime.fromisoformat(row["observed_at"]),
                valid_until=datetime.fromisoformat(row["valid_until"]),
            )
            for row in rows
        )

    def add_evidence(
        self,
        *,
        tenant_id: str,
        task_id: str,
        item_id: str | None,
        field_name: str,
        value: Any,
        source_type: str,
        source_id: str,
        locator: str,
        observed_at: datetime,
        valid_until: datetime | None,
        confidence: Decimal,
        producer: str,
    ) -> str:
        evidence_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evidence(
                    evidence_id, tenant_id, task_id, item_id, field_name,
                    source_type, source_id, locator, observed_at, valid_until,
                    confidence, producer, value_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    tenant_id,
                    task_id,
                    item_id,
                    field_name,
                    source_type,
                    source_id,
                    locator,
                    observed_at.isoformat(),
                    valid_until.isoformat() if valid_until else None,
                    str(confidence),
                    producer,
                    canonical_hash(value),
                ),
            )
        return evidence_id

    def evidence_for_task(
        self,
        *,
        tenant_id: str,
        task_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence
                WHERE tenant_id=? AND task_id=? ORDER BY rowid
                """,
                (tenant_id, task_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def save_approval(self, approval: ApprovalGrant) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO approval_grants(
                    approval_id, tenant_id, task_id, action, subject_hash,
                    approved_by, approved_by_roles_json, approved_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.tenant_id,
                    approval.task_id,
                    approval.action,
                    approval.subject_hash,
                    approval.approved_by,
                    _json(sorted(approval.approved_by_roles)),
                    approval.approved_at.isoformat(),
                    approval.expires_at.isoformat(),
                ),
            )

    def create_po_draft(
        self,
        *,
        tenant_id: str,
        task_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        total_amount: Decimal,
        currency: str,
    ) -> tuple[dict[str, Any], bool]:
        request_hash = canonical_hash(payload)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM po_drafts
                WHERE tenant_id=? AND idempotency_key=?
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "PO idempotency key reused with different payload"
                    )
                return dict(existing), True
            po_draft_id = str(uuid4())
            created_at = utc_now().isoformat()
            connection.execute(
                """
                INSERT INTO po_drafts(
                    po_draft_id, tenant_id, task_id, idempotency_key,
                    request_hash, payload_json, total_amount, currency, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    po_draft_id,
                    tenant_id,
                    task_id,
                    idempotency_key,
                    request_hash,
                    _json(payload),
                    str(total_amount),
                    currency,
                    created_at,
                ),
            )
        return {
            "po_draft_id": po_draft_id,
            "tenant_id": tenant_id,
            "task_id": task_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "payload_json": _json(payload),
            "total_amount": str(total_amount),
            "currency": currency,
            "created_at": created_at,
        }, False

    def po_draft_for_task(
        self,
        *,
        tenant_id: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM po_drafts
                WHERE tenant_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1
                """,
                (tenant_id, task_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def append_workflow_event(
        self,
        *,
        tenant_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        event_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO workflow_events(
                    event_id, tenant_id, task_id, event_type,
                    payload_hash, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    tenant_id,
                    task_id,
                    event_type,
                    canonical_hash(payload),
                    _json(payload),
                    utc_now().isoformat(),
                ),
            )
        return event_id

    def workflow_events(
        self,
        *,
        tenant_id: str,
        task_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_events
                WHERE tenant_id=? AND task_id=? ORDER BY sequence
                """,
                (tenant_id, task_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)
