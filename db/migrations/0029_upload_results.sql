-- Per-file outcome of each upload, so the page can show it after the redirect without riding in the (4 KB) session cookie.
-- Operational log only: the last few batches are kept, the raw rows live in source_files/transactions/budget_items.
CREATE TABLE IF NOT EXISTS upload_batches (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  uploaded_by TEXT,
  results     JSONB NOT NULL          -- [{"name","source","ok","detail"}]
);
