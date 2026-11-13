-- #153, part 2: the yearly withholding check. One signed amount per tax year: a refund is positive, a bill (tax owed) negative. The
-- annual check-in's "Record last year's tax outcome" item takes it (#157); the year in review shows the trend. A reference table:
-- settings.zip carries it as tax_outcomes.json and the seed loader reads it back. The checklist item that takes it is seeded by 0157.
CREATE TABLE IF NOT EXISTS tax_outcomes (
  year        CHAR(4) PRIMARY KEY CHECK (year ~ '^\d{4}$'),
  amount      NUMERIC(14, 2) NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT NOT NULL DEFAULT ''
);
