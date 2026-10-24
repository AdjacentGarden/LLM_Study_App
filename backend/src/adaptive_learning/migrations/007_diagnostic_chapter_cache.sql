CREATE TABLE IF NOT EXISTS diagnostic_chapter_cache (
    cache_key TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('running','approved','rejected','not_admitted','unknown')),
    claim_token TEXT NOT NULL,
    items_json TEXT,
    items_sha256 TEXT,
    recovery_epoch INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostic_chapter_submissions (
    token TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL REFERENCES diagnostic_chapter_cache(cache_key),
    state TEXT NOT NULL CHECK(state IN ('running','approved','rejected','not_admitted','unknown')),
    recovery_epoch INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnostic_chapter_submission
    ON diagnostic_chapter_submissions(cache_key,created_at);
CREATE INDEX IF NOT EXISTS idx_diagnostic_chapter_scope
    ON diagnostic_chapter_cache(scope,state);
CREATE TABLE IF NOT EXISTS diagnostic_recovery_epochs (
    scope TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL,
    failed_marker TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostic_recovery_grants (
    scope TEXT NOT NULL,
    marker TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(scope,marker)
);
