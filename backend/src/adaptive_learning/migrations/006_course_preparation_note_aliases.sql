-- Durable identities/queues from the optimized single-user branch. No live
-- process ownership is imported; startup recovery still requires the worker lock.
CREATE TABLE IF NOT EXISTS course_jobs (
    job_id TEXT PRIMARY KEY,
    request_key TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    result TEXT,
    created DOUBLE PRECISION NOT NULL,
    updated DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS course_jobs_queue ON course_jobs(status,created,job_id);

CREATE TABLE IF NOT EXISTS course_job_requests (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    PRIMARY KEY(session_id,request_id)
);

CREATE TABLE IF NOT EXISTS studio_note_request_aliases (
    owner TEXT NOT NULL,
    request_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_json TEXT NOT NULL,
    job_id TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(owner,request_id)
);

CREATE TABLE IF NOT EXISTS preparation (
    book_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    updated DOUBLE PRECISION NOT NULL,
    next_step INTEGER NOT NULL DEFAULT 0
);
