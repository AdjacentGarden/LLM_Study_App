CREATE TABLE IF NOT EXISTS assistant_history_initializations (owner TEXT PRIMARY KEY,initialized DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS assistant_conversations (
    owner TEXT NOT NULL,session_id TEXT NOT NULL,title TEXT NOT NULL,
    state TEXT NOT NULL,input_tokens BIGINT NOT NULL,estimated INTEGER NOT NULL,
    active_run_id TEXT,reason TEXT,created DOUBLE PRECISION NOT NULL,updated DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(owner,session_id));
CREATE TABLE IF NOT EXISTS assistant_conversation_events (
    owner TEXT NOT NULL,session_id TEXT NOT NULL,event_id TEXT NOT NULL,
    payload TEXT NOT NULL,created DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(owner,session_id,event_id));
CREATE TABLE IF NOT EXISTS assistant_conversation_runs (
    owner TEXT NOT NULL,session_id TEXT NOT NULL,run_id TEXT NOT NULL,
    PRIMARY KEY(owner,run_id));
CREATE TABLE IF NOT EXISTS native_learning_objects (
    id TEXT PRIMARY KEY,owner TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES native_workspaces(id) ON DELETE CASCADE,
    course_resource_id TEXT,kind TEXT NOT NULL,revision INTEGER NOT NULL,
    payload TEXT NOT NULL,created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS native_assistant_operations (
    owner TEXT NOT NULL,operation_id TEXT NOT NULL,digest TEXT NOT NULL,
    receipt TEXT NOT NULL,created DOUBLE PRECISION NOT NULL,PRIMARY KEY(owner,operation_id));
CREATE TABLE IF NOT EXISTS native_workspace_tombstones (
    owner TEXT NOT NULL,workspace_id TEXT NOT NULL,deleted DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(owner,workspace_id));
