ALTER TABLE task_uploads
ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 1 CHECK (ordinal >= 1);

ALTER TABLE procurement_tasks ADD COLUMN deleted_at TEXT;
ALTER TABLE procurement_tasks ADD COLUMN deleted_by TEXT;
ALTER TABLE procurement_tasks ADD COLUMN deletion_reason TEXT;

CREATE INDEX idx_tasks_active
ON procurement_tasks(tenant_id, deleted_at, updated_at DESC);
