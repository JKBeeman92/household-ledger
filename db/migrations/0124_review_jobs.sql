-- Month-close reviews run in the background (issue #124): one row per request, its outcome kept for the page and the editor.
CREATE TABLE IF NOT EXISTS review_jobs (
  id           BIGSERIAL PRIMARY KEY,
  month        CHAR(7) NOT NULL,
  state        TEXT NOT NULL CHECK (state IN ('queued', 'running', 'stored', 'failed')),
  requested_by TEXT,
  provider     TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at   TIMESTAMPTZ,
  finished_at  TIMESTAMPTZ,
  sections     INT,
  error        TEXT
);
-- one review in flight per month: a second click, or a second tab, cannot send (and pay for) the month again
CREATE UNIQUE INDEX IF NOT EXISTS review_jobs_one_active ON review_jobs (month) WHERE state IN ('queued', 'running');
