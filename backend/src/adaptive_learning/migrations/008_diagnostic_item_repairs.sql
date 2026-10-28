CREATE TABLE IF NOT EXISTS diagnostic_item_repairs (
    cache_key TEXT PRIMARY KEY REFERENCES diagnostic_chapter_cache(cache_key),
    state_json TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE preparation ADD COLUMN IF NOT EXISTS retry_at DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE preparation ADD COLUMN IF NOT EXISTS details TEXT NOT NULL DEFAULT '{}';
