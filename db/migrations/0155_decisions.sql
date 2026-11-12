-- #155: a decisions log. What the household decided at a check-in, and month by month whether it happened.
-- Reference tables (the household types them), so they travel in settings.zip and are read back by the seed loader.

CREATE TABLE IF NOT EXISTS decisions (
  id          BIGSERIAL PRIMARY KEY,
  made_in     CHAR(7) NOT NULL CHECK (made_in ~ '^\d{4}-(0[1-9]|1[0-2])$'),        -- the check-in's month; in force from the month after
  text        TEXT NOT NULL CHECK (length(btrim(text)) BETWEEN 1 AND 300),          -- in the household's words
  kind        TEXT NOT NULL CHECK (kind IN ('spend_cap', 'extra_payment', 'save', 'plan_change', 'other')),
  target      JSONB NULL,                                                           -- machine-checkable, optional (pipeline/decisions.py)
  until       CHAR(7) NULL CHECK (until IS NULL OR until ~ '^\d{4}-(0[1-9]|1[0-2])$'),   -- the last month in force; NULL: one month
  person      TEXT NULL,                                                            -- who owns it (#156), one of the people rule
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'kept', 'partly', 'dropped', 'done')),
  note        TEXT NOT NULL DEFAULT '',                                             -- private: never sent to the review or MCP
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by  TEXT NOT NULL DEFAULT '',
  CHECK (until IS NULL OR until > made_in)
);
CREATE INDEX IF NOT EXISTS decisions_made_in ON decisions (made_in);

CREATE TABLE IF NOT EXISTS decision_checks (
  decision_id   BIGINT NOT NULL REFERENCES decisions (id) ON DELETE CASCADE,
  month         CHAR(7) NOT NULL CHECK (month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  verdict       TEXT NULL CHECK (verdict IS NULL OR verdict IN ('kept', 'partly', 'dropped')),   -- the household's
  auto_verdict  TEXT NOT NULL CHECK (auto_verdict IN ('kept', 'partly', 'dropped', 'pending', 'unknown')),   -- the app's, when confirmed
  auto_figure   NUMERIC(14, 2) NULL,                                                -- what the app's verdict rested on ("Eating Out was $372")
  checked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  checked_by    TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (decision_id, month)
);

-- an account rename is logged like a category's (#155: decisions name accounts in an extra payment's target)
ALTER TABLE renames DROP CONSTRAINT IF EXISTS renames_dimension_check;
ALTER TABLE renames ADD CONSTRAINT renames_dimension_check CHECK (dimension IN ('category', 'canonical_item', 'account'));
