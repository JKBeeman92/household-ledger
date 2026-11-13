-- #158: planned purchases. A goal may have an amount and a budget month to reach it by, and say where the money is kept: a budget
-- envelope (a canonical item, the default) or an account (#143). Goals without them are unchanged. since_month is the first budget
-- month whose envelope saving counts; done_at and done_month record that it was bought (the check-in asks once it is reached).
-- The goals table is a reference table: the new columns travel in settings.zip's goals.json and are read back by the seed loader.
ALTER TABLE goals ADD COLUMN IF NOT EXISTS target_amount NUMERIC(14, 2) NULL CHECK (target_amount IS NULL OR target_amount > 0);
ALTER TABLE goals ADD COLUMN IF NOT EXISTS target_month  CHAR(7) NULL CHECK (target_month IS NULL OR target_month ~ '^\d{4}-(0[1-9]|1[0-2])$');
ALTER TABLE goals ADD COLUMN IF NOT EXISTS funded_by     TEXT NULL;
ALTER TABLE goals ADD COLUMN IF NOT EXISTS funded_kind   TEXT NULL CHECK (funded_kind IS NULL OR funded_kind IN ('envelope', 'account'));
ALTER TABLE goals ADD COLUMN IF NOT EXISTS since_month   CHAR(7) NULL CHECK (since_month IS NULL OR since_month ~ '^\d{4}-(0[1-9]|1[0-2])$');
ALTER TABLE goals ADD COLUMN IF NOT EXISTS done_at       TIMESTAMPTZ NULL;
ALTER TABLE goals ADD COLUMN IF NOT EXISTS done_month    CHAR(7) NULL CHECK (done_month IS NULL OR done_month ~ '^\d{4}-(0[1-9]|1[0-2])$');
ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_dated_pair;
ALTER TABLE goals ADD CONSTRAINT goals_dated_pair CHECK ((target_amount IS NULL) = (target_month IS NULL));
