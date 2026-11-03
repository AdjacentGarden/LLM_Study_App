-- Server-side lifecycle and budget ledger for phone-driven assistant runs.
CREATE TABLE IF NOT EXISTS assistant_runs (
    owner TEXT NOT NULL,
    run_id TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    posts INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    model_seconds DOUBLE PRECISION NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    updated DOUBLE PRECISION NOT NULL,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(owner,run_id)
);
CREATE TABLE IF NOT EXISTS assistant_steps (
    owner TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    lease_until DOUBLE PRECISION NOT NULL,
    message_json TEXT,
    finish_reason TEXT,
    error TEXT,
    retry_after DOUBLE PRECISION,
    estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
    expire_charged_input_tokens INTEGER NOT NULL DEFAULT 0,
    expire_charged_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    expire_charged_posts INTEGER NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL,
    updated DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(owner,run_id,step_id),
    UNIQUE(owner,run_id,step_index),
    FOREIGN KEY(owner,run_id) REFERENCES assistant_runs(owner,run_id)
);
