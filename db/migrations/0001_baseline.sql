-- Baseline schema. Idempotent on purpose: a database created before migrations existed adopts it without change.
-- Later changes go in new numbered files (see AGENTS.md), never in here.
-- Raw exports are immutable facts; everything derived is rebuilt by the pipeline.

CREATE TABLE IF NOT EXISTS source_files (
  id            BIGSERIAL PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('transactions','budget_items')),
  budget_month  CHAR(7) NOT NULL,              -- YYYY-MM
  filename      TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  row_count     INTEGER NOT NULL,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  uploaded_by   TEXT,
  UNIQUE (kind, budget_month)                  -- re-uploading a month replaces it
);

CREATE TABLE IF NOT EXISTS transactions (
  id             BIGSERIAL PRIMARY KEY,
  source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
  budget_month   CHAR(7) NOT NULL,
  row_no         INTEGER NOT NULL,
  grp            TEXT NOT NULL,
  item           TEXT NOT NULL,
  type           TEXT NOT NULL,
  date           DATE NOT NULL,
  merchant       TEXT NOT NULL DEFAULT '',
  account        TEXT NOT NULL DEFAULT '',
  amount         NUMERIC(12,2) NOT NULL,
  note           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS transactions_bm ON transactions (budget_month);

CREATE TABLE IF NOT EXISTS budget_items (
  id             BIGSERIAL PRIMARY KEY,
  source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
  budget_month   CHAR(7) NOT NULL,
  grp            TEXT NOT NULL,
  item           TEXT NOT NULL,
  planned        NUMERIC(12,2) NOT NULL,
  spent          NUMERIC(12,2) NOT NULL,
  remaining      NUMERIC(12,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS budget_items_bm ON budget_items (budget_month);

-- Reference tables: the user's decisions. Seeded from pipeline/*.csv|json on first boot, then owned by the DB.
CREATE TABLE IF NOT EXISTS item_mapping (
  item            TEXT PRIMARY KEY,            -- normalized EveryDollar item name
  canonical_item  TEXT NOT NULL,
  category        TEXT NOT NULL,
  flow            TEXT NOT NULL CHECK (flow IN ('income','expense','debt','transfer','plug','zero','exclude','verify')),
  note            TEXT NOT NULL DEFAULT '',
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cpi (
  month   CHAR(7) PRIMARY KEY,
  cpi     NUMERIC(9,3) NOT NULL,
  source  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
  key    TEXT PRIMARY KEY,
  value  NUMERIC(14,4),
  unit   TEXT NOT NULL DEFAULT '',
  label  TEXT NOT NULL,
  note   TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS life_events (
  month  CHAR(7) NOT NULL,
  label  TEXT NOT NULL,
  PRIMARY KEY (month, label)
);

-- Derived: the canonical ledger, rebuilt on every load.
CREATE TABLE IF NOT EXISTS ledger (
  budget_month   CHAR(7) NOT NULL,
  calendar_month CHAR(7) NOT NULL,
  date           DATE NOT NULL,
  grp            TEXT NOT NULL,
  item           TEXT NOT NULL,
  type           TEXT NOT NULL,
  canonical_item TEXT NOT NULL,
  category       TEXT NOT NULL,
  flow           TEXT NOT NULL,
  merchant       TEXT NOT NULL,
  merchant_norm  TEXT NOT NULL,
  amount         NUMERIC(12,2) NOT NULL,
  amount_real    NUMERIC(12,2) NOT NULL,
  earner         TEXT NOT NULL DEFAULT '',
  payer          TEXT NOT NULL DEFAULT '',
  employer       TEXT NOT NULL DEFAULT '',
  is_transfer    BOOLEAN NOT NULL,
  is_plug        BOOLEAN NOT NULL,
  transfer_pair  INTEGER,
  in_totals      BOOLEAN NOT NULL,
  has_note       BOOLEAN NOT NULL,
  note           TEXT NOT NULL DEFAULT '',
  source_file    TEXT NOT NULL,
  source_row     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_bm ON ledger (budget_month);

-- Derived: the dashboard's datasets, one JSON array per name, rebuilt on every load.
CREATE TABLE IF NOT EXISTS datasets (
  name        TEXT PRIMARY KEY,
  rows        JSONB NOT NULL,
  built_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  row_count   INTEGER NOT NULL
);

-- Month-close reviews written by the model. One per month; regenerating replaces.
CREATE TABLE IF NOT EXISTS reviews (
  month         CHAR(7) PRIMARY KEY,
  generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  model         TEXT NOT NULL,
  sections      JSONB NOT NULL,                -- [{order, heading, text}]
  prompt        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS build_log (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  ok          BOOLEAN,
  summary     TEXT,
  detail      TEXT
);

-- Free-form settings: 'rules' (pipeline rules JSON), 'household' (name, theme, cpi base), etc.
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
