-- #153: estimated tax at the check-in. A row is a quarter's estimated payment the household ticked as paid at that quarter's
-- check-in (the quarter-end budget month, as checkins.month in quarter mode); unticking deletes it. A reference table: it travels
-- in settings.zip as tax_payments.json and is read back by the seed loader. The deadline, label and expected amount are kept as
-- they stood when ticked, since the rules and the goal can change afterwards.
CREATE TABLE IF NOT EXISTS tax_payments (
  month       CHAR(7) PRIMARY KEY CHECK (month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  deadline_m  CHAR(7) NULL,
  label       TEXT NOT NULL DEFAULT '',
  expected    NUMERIC(14, 2) NULL,
  amount      NUMERIC(14, 2) NULL CHECK (amount IS NULL OR amount >= 0),
  note        TEXT NOT NULL DEFAULT '',
  paid_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_by     TEXT NOT NULL DEFAULT ''
);
