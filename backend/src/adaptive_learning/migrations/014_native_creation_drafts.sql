-- Persist course creation drafts before their final confirmation.
CREATE TABLE IF NOT EXISTS native_creation_drafts (
    workspace_id TEXT PRIMARY KEY REFERENCES native_workspaces(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    updated DOUBLE PRECISION NOT NULL);
