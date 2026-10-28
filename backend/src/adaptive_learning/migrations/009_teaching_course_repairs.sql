ALTER TABLE course_jobs ADD COLUMN IF NOT EXISTS retry_at DOUBLE PRECISION NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS teaching_course_repairs (
    run_key TEXT PRIMARY KEY, scope TEXT NOT NULL, chapter_id TEXT NOT NULL,
    identity_json TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}', updated DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS teaching_course_scope ON teaching_course_repairs(scope,chapter_id,updated);
CREATE TABLE IF NOT EXISTS teaching_course_units (
    run_key TEXT NOT NULL, unit_id TEXT NOT NULL, candidate_json TEXT NOT NULL,
    candidate_hash TEXT NOT NULL, status TEXT NOT NULL, feedback_json TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0, next_retry_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated DOUBLE PRECISION NOT NULL, PRIMARY KEY(run_key,unit_id)
);
CREATE TABLE IF NOT EXISTS teaching_course_proofs (
    run_key TEXT NOT NULL, proof_key TEXT NOT NULL, proof_json TEXT NOT NULL,
    updated DOUBLE PRECISION NOT NULL, PRIMARY KEY(run_key,proof_key)
);
CREATE TABLE IF NOT EXISTS teaching_course_submissions (
    run_key TEXT NOT NULL, request_key TEXT NOT NULL, token TEXT NOT NULL,
    status TEXT NOT NULL, response_json TEXT, updated DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(run_key,request_key)
);
