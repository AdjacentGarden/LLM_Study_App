create extension if not exists vector;

create table if not exists rag_index_state (
  book_id text primary key,
  index_generation bigint not null check (index_generation > 0),
  build_id text not null,
  state text not null check (
    state in ('building', 'committed', 'ready', 'cache_failed', 'failed', 'deleted')
  ),
  embedding_model text not null,
  embedding_revision text not null,
  chunk_version text not null,
  embedding_dimension integer not null check (embedding_dimension = 1024),
  chunk_count integer not null default 0 check (chunk_count >= 0),
  error text,
  reserved_at timestamptz not null default now(),
  committed_at timestamptz,
  ready_at timestamptz,
  deleted_at timestamptz,
  updated_at timestamptz not null default now()
);

create table if not exists rag_chunk_vectors (
  book_id text not null,
  index_generation bigint not null check (index_generation > 0),
  chunk_id text not null,
  chapter_id text not null,
  page_start integer not null,
  page_end integer not null,
  content_type text not null,
  text text not null,
  asset_ids jsonb not null default '[]'::jsonb,
  key_concepts jsonb not null default '[]'::jsonb,
  parser text,
  parser_version text,
  chunk_version text not null,
  heading_path jsonb not null default '[]'::jsonb,
  source_block_ids jsonb not null default '[]'::jsonb,
  quality_score double precision,
  token_count integer,
  content_hash text,
  bbox jsonb,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1024) not null,
  created_at timestamptz not null default now(),
  primary key (book_id, index_generation, chunk_id),
  check (page_start > 0 and page_end >= page_start),
  check (jsonb_typeof(asset_ids) = 'array'),
  check (jsonb_typeof(key_concepts) = 'array'),
  check (jsonb_typeof(heading_path) = 'array'),
  check (jsonb_typeof(source_block_ids) = 'array'),
  check (bbox is null or jsonb_typeof(bbox) = 'array'),
  check (jsonb_typeof(metadata) = 'object')
);

create index if not exists rag_chunk_vectors_book_generation_chapter_idx
  on rag_chunk_vectors (book_id, index_generation, chapter_id);

create index if not exists rag_chunk_vectors_embedding_hnsw_idx
  on rag_chunk_vectors using hnsw (embedding vector_cosine_ops);
