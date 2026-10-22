CREATE TABLE IF NOT EXISTS studio_note_editions (
    job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, note_id TEXT NOT NULL,
    accepted INTEGER NOT NULL, updated DOUBLE PRECISION NOT NULL
);
ALTER TABLE studio_note_editions ALTER COLUMN updated TYPE DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS studio_editions_owner ON studio_note_editions(owner,note_id);
