CREATE TABLE tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    tenant_pack_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE products (
    product_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    compatibility_tags_json TEXT NOT NULL DEFAULT '[]',
    unit TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (tenant_id, product_id),
    UNIQUE (tenant_id, sku),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE suppliers (
    supplier_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    risk_level TEXT NOT NULL,
    PRIMARY KEY (tenant_id, supplier_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE quotations (
    quotation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    currency TEXT NOT NULL,
    tax_rate TEXT NOT NULL,
    freight TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    PRIMARY KEY (tenant_id, quotation_id),
    FOREIGN KEY (tenant_id, supplier_id)
        REFERENCES suppliers(tenant_id, supplier_id),
    FOREIGN KEY (tenant_id, product_id)
        REFERENCES products(tenant_id, product_id)
);

CREATE TABLE inventory (
    tenant_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    PRIMARY KEY (tenant_id, supplier_id, product_id),
    FOREIGN KEY (tenant_id, supplier_id)
        REFERENCES suppliers(tenant_id, supplier_id),
    FOREIGN KEY (tenant_id, product_id)
        REFERENCES products(tenant_id, product_id)
);

CREATE TABLE procurement_tasks (
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, task_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE task_items (
    item_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    description TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit TEXT NOT NULL,
    requested_part_number TEXT,
    matched_product_id TEXT,
    match_confidence TEXT,
    selected_supplier_id TEXT,
    selected_quotation_id TEXT,
    PRIMARY KEY (tenant_id, item_id),
    UNIQUE (tenant_id, task_id, line_number),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id),
    FOREIGN KEY (tenant_id, matched_product_id)
        REFERENCES products(tenant_id, product_id)
);

CREATE TABLE evidence (
    evidence_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    item_id TEXT,
    field_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    locator TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT,
    confidence TEXT NOT NULL,
    producer TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    PRIMARY KEY (tenant_id, evidence_id),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE TABLE approval_grants (
    approval_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_hash TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, approval_id),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE TABLE po_drafts (
    po_draft_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    total_amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, po_draft_id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE TABLE workflow_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE INDEX idx_products_tenant_name ON products(tenant_id, name);
CREATE INDEX idx_tasks_tenant_status ON procurement_tasks(tenant_id, status);
CREATE INDEX idx_evidence_task ON evidence(tenant_id, task_id, field_name);
CREATE INDEX idx_workflow_events_task ON workflow_events(tenant_id, task_id, sequence);
