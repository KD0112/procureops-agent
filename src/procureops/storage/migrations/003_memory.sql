CREATE TABLE memory_records (
    record_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('candidate', 'confirmed', 'corrected', 'deleted', 'expired')
    ),
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('non_sensitive', 'sensitive')),
    confidence TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    confirmed_by TEXT,
    replaces_record_id TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    expires_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE INDEX idx_memory_active
ON memory_records(tenant_id, user_id, status, memory_key, expires_at);
