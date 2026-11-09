-- Outbound hooks (#35): a log of every webhook delivery attempt (shown on the Webhooks page), and the months already announced as
-- missing so months.missing fires at most once per missing month. The URL is kept as host only (paths often carry tokens); no body or response is stored.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id           BIGSERIAL PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  event        TEXT NOT NULL,
  month        TEXT,
  target       TEXT NOT NULL,
  ok           BOOLEAN NOT NULL,
  http_status  INTEGER,
  error        TEXT,
  duration_ms  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS webhook_deliveries_created_idx ON webhook_deliveries (created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS webhook_months_notified (
  month        TEXT PRIMARY KEY,
  notified_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
