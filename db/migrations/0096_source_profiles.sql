-- Sources other than EveryDollar (issue #96). A profile maps another tool's transactions and budget files onto the internal
-- (EveryDollar-shaped) rows; its spec is the JSON documented in pipeline/sources.py.
CREATE TABLE IF NOT EXISTS source_profiles (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  spec        JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A mapped source keeps the file exactly as uploaded (the fact) and the profile its rows were read with. Its transactions and
-- budget_items rows are derived from content and are re-derived when the profile is corrected. EveryDollar files keep both
-- columns NULL and behave exactly as before; existing rows need no data migration.
ALTER TABLE source_files ADD COLUMN IF NOT EXISTS profile_id BIGINT REFERENCES source_profiles(id) ON DELETE RESTRICT;
ALTER TABLE source_files ADD COLUMN IF NOT EXISTS content BYTEA;
ALTER TABLE source_files ADD CONSTRAINT source_files_mapped_has_content CHECK (profile_id IS NULL OR content IS NOT NULL);

-- Files waiting for the mapping step on the upload page; short-lived (the app clears entries older than a day).
CREATE TABLE IF NOT EXISTS upload_pending (
  id          BIGSERIAL PRIMARY KEY,
  batch       TEXT NOT NULL,
  filename    TEXT NOT NULL,
  data        BYTEA NOT NULL,
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS upload_pending_batch_idx ON upload_pending (batch);
