ALTER TABLE memory_records ADD COLUMN source_hash TEXT;

CREATE INDEX idx_memory_source_dedup
ON memory_records(tenant_id, user_id, memory_key, source_hash, status);

CREATE TABLE user_feedback (
    tenant_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_id TEXT,
    feedback_type TEXT NOT NULL CHECK (
        feedback_type IN ('correction', 'preference', 'failure', 'rating')
    ),
    summary TEXT NOT NULL,
    correction_json TEXT NOT NULL DEFAULT '{}',
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'linked', 'resolved')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (tenant_id, feedback_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES procurement_tasks(tenant_id, task_id)
);

CREATE INDEX idx_feedback_workbench
ON user_feedback(tenant_id, status, created_at);

CREATE INDEX idx_feedback_user
ON user_feedback(tenant_id, user_id, created_at);

CREATE TABLE prompt_candidates (
    tenant_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    base_version TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'proposed', 'evaluated', 'approved', 'rejected',
            'released', 'rolled_back'
        )
    ),
    evaluation_mode TEXT,
    evaluation_report_json TEXT,
    evaluation_passed INTEGER CHECK (evaluation_passed IN (0, 1)),
    safety_passed INTEGER CHECK (safety_passed IN (0, 1)),
    proposed_by TEXT NOT NULL,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    evaluated_at TEXT,
    approved_at TEXT,
    rejected_at TEXT,
    released_at TEXT,
    PRIMARY KEY (tenant_id, candidate_id),
    UNIQUE (tenant_id, scope, candidate_version),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE INDEX idx_prompt_candidates_workbench
ON prompt_candidates(tenant_id, scope, status, created_at);

CREATE TABLE candidate_feedback (
    tenant_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, candidate_id, feedback_id),
    FOREIGN KEY (tenant_id, candidate_id)
        REFERENCES prompt_candidates(tenant_id, candidate_id),
    FOREIGN KEY (tenant_id, feedback_id)
        REFERENCES user_feedback(tenant_id, feedback_id)
);

CREATE TABLE prompt_releases (
    tenant_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'rolled_back')),
    previous_release_id TEXT,
    released_by TEXT NOT NULL,
    released_at TEXT NOT NULL,
    rolled_back_by TEXT,
    rolled_back_at TEXT,
    PRIMARY KEY (tenant_id, release_id),
    UNIQUE (tenant_id, scope, prompt_version),
    FOREIGN KEY (tenant_id, candidate_id)
        REFERENCES prompt_candidates(tenant_id, candidate_id),
    FOREIGN KEY (tenant_id, previous_release_id)
        REFERENCES prompt_releases(tenant_id, release_id)
);

CREATE UNIQUE INDEX idx_prompt_releases_one_active
ON prompt_releases(tenant_id, scope)
WHERE status = 'active';

CREATE INDEX idx_prompt_releases_history
ON prompt_releases(tenant_id, scope, released_at);
