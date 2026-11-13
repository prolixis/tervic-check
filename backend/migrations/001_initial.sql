CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX auth_sessions_user_id_idx ON auth_sessions(user_id);
CREATE INDEX auth_sessions_expires_at_idx ON auth_sessions(expires_at);

CREATE TABLE scans (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(32) NOT NULL,
    workflow_name VARCHAR(500),
    workflow_external_id VARCHAR(255),
    workflow_node_count INTEGER CHECK (workflow_node_count IS NULL OR workflow_node_count >= 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    total_findings INTEGER NOT NULL DEFAULT 0 CHECK (total_findings >= 0),
    result JSONB,
    error_code VARCHAR(80),
    CHECK (
        (status = 'running' AND completed_at IS NULL) OR
        (status IN ('completed', 'failed') AND completed_at IS NOT NULL)
    )
);

CREATE INDEX scans_user_started_at_idx ON scans(user_id, started_at DESC);
CREATE INDEX scans_user_status_idx ON scans(user_id, status);

CREATE TABLE scan_findings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scan_id UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    finding_index INTEGER NOT NULL CHECK (finding_index >= 0),
    rule_id VARCHAR(120) NOT NULL,
    severity VARCHAR(32),
    title TEXT,
    message TEXT,
    node_id TEXT,
    node_name TEXT,
    location TEXT,
    certainty VARCHAR(32),
    recommendation TEXT,
    details JSONB NOT NULL,
    UNIQUE (scan_id, finding_index)
);

CREATE INDEX scan_findings_scan_id_idx ON scan_findings(scan_id, finding_index);
