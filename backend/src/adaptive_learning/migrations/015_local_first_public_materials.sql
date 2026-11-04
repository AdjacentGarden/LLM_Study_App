-- Shared basic textbook groups, never personal course payloads.
CREATE TABLE IF NOT EXISTS public_course_groups (
    id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,
    description TEXT NOT NULL,book_ids TEXT NOT NULL,status TEXT NOT NULL,
    manifest TEXT,error TEXT,active INTEGER NOT NULL DEFAULT 1,
    legacy_post TEXT,created DOUBLE PRECISION NOT NULL,updated DOUBLE PRECISION NOT NULL);
-- Digest-only acknowledgement of a user's private export, not the export body.
CREATE TABLE IF NOT EXISTS private_export_checkpoints (
    owner TEXT PRIMARY KEY,snapshot_revision TEXT NOT NULL,digest TEXT NOT NULL,
    record_count INTEGER NOT NULL,file_count INTEGER NOT NULL,updated DOUBLE PRECISION NOT NULL);
