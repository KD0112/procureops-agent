CREATE TABLE logistics_quotes (
    tenant_id TEXT NOT NULL,
    logistics_quote_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    shipping_method TEXT NOT NULL,
    lead_time_days INTEGER NOT NULL CHECK (lead_time_days >= 0),
    shipping_cost TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    PRIMARY KEY (tenant_id, logistics_quote_id),
    UNIQUE (tenant_id, supplier_id, product_id),
    FOREIGN KEY (tenant_id, supplier_id)
        REFERENCES suppliers(tenant_id, supplier_id),
    FOREIGN KEY (tenant_id, product_id)
        REFERENCES products(tenant_id, product_id)
);

CREATE INDEX idx_logistics_lookup
ON logistics_quotes(tenant_id, product_id, valid_until, supplier_id);

ALTER TABLE memory_records ADD COLUMN integrity_hash TEXT;

CREATE TABLE memory_access_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    record_id TEXT,
    action TEXT NOT NULL CHECK (
        action IN ('propose', 'confirm', 'read', 'correct', 'delete', 'rejected')
    ),
    decision TEXT NOT NULL,
    metadata_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE INDEX idx_memory_access_user
ON memory_access_events(tenant_id, user_id, occurred_at);

CREATE TABLE local_users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE tenant_memberships (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, user_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (user_id) REFERENCES local_users(user_id)
);

CREATE INDEX idx_memberships_user
ON tenant_memberships(user_id, active, tenant_id);

CREATE TABLE auth_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES local_users(user_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES tenant_memberships(tenant_id, user_id)
);

CREATE INDEX idx_auth_sessions_active
ON auth_sessions(token_hash, expires_at, revoked_at);

CREATE TABLE outbox_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'dispatching', 'dispatched')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error_class TEXT,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE INDEX idx_outbox_dispatch
ON outbox_events(status, created_at, event_id);
