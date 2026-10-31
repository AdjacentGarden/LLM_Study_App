-- Group metadata is independent of immutable input revisions and model jobs.
CREATE TABLE IF NOT EXISTS studio_note_logical_inputs (
    note_id TEXT PRIMARY KEY REFERENCES studio_notes(id),
    owner TEXT NOT NULL,
    book_id TEXT NOT NULL,
    logical_note_id TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))
);
CREATE INDEX IF NOT EXISTS studio_note_logical_owner
    ON studio_note_logical_inputs(owner,book_id,logical_note_id);
