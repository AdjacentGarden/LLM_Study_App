-- Ephemeral readiness; never import old process heartbeats as live workers.
CREATE TABLE IF NOT EXISTS worker_presence (
    instance_id TEXT NOT NULL, queue TEXT NOT NULL,
    updated DOUBLE PRECISION NOT NULL, expires DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(instance_id,queue)
);
CREATE INDEX IF NOT EXISTS worker_presence_queue ON worker_presence(queue,expires);
