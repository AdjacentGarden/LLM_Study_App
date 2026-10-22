-- SQLite INTEGER message IDs are signed 64-bit; read cursors must preserve them.
ALTER TABLE chat_reads ALTER COLUMN last_id TYPE BIGINT;
