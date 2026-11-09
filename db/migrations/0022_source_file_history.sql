-- What a month's files used to be. A row is written when a source file is replaced by a re-upload or deleted from the UI,
-- keeping the old file's sha256 so a bad re-upload can be noticed. The raw rows themselves are never kept or edited.
CREATE TABLE IF NOT EXISTS source_file_history (
  id            BIGSERIAL PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('transactions','budget_items')),
  budget_month  CHAR(7) NOT NULL,
  filename      TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  row_count     INTEGER NOT NULL,
  uploaded_at   TIMESTAMPTZ NOT NULL,          -- when the old file was uploaded
  uploaded_by   TEXT,
  event         TEXT NOT NULL CHECK (event IN ('replaced','deleted')),
  event_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_by      TEXT
);
CREATE INDEX IF NOT EXISTS source_file_history_month_idx ON source_file_history (budget_month, kind, event_at DESC);
