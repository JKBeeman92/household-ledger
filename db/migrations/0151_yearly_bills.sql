-- #151: irregular yearly bills. The pipeline detects them (pipeline/yearly_bills.py); this table is the household's own list:
-- a detected bill confirmed (with its month, amount, cadence and funding), one added by hand, or one dismissed for good.
CREATE TABLE IF NOT EXISTS yearly_bills (
  item         TEXT PRIMARY KEY REFERENCES canonical_items (name) ON UPDATE CASCADE ON DELETE CASCADE,
  month        SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),               -- the calendar month it usually falls in
  amount       NUMERIC(14, 2) NULL CHECK (amount IS NULL OR amount >= 0),     -- NULL: last year's, as detected
  every_years  SMALLINT NOT NULL DEFAULT 1 CHECK (every_years BETWEEN 1 AND 10),
  funded_by    TEXT NULL,                                                     -- a canonical item (the envelope) or an account; NULL: the guess
  source       TEXT NOT NULL CHECK (source IN ('confirmed', 'added', 'dismissed')),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by   TEXT NOT NULL DEFAULT ''
);
