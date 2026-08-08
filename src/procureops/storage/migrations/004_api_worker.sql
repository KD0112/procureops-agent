CREATE TABLE task_uploads (
    upload_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, upload_id),
    UNIQUE (tenant_id, storage_key),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE TABLE work_queue (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'leased', 'retry', 'succeeded', 'dead_letter')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error_class TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE INDEX idx_uploads_task
ON task_uploads(tenant_id, task_id, created_at);

CREATE INDEX idx_work_queue_claim
ON work_queue(status, available_at, lease_expires_at, created_at);

CREATE INDEX idx_work_queue_task
ON work_queue(tenant_id, task_id, created_at);
