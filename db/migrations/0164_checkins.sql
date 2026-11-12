-- Issue #164: the monthly check-in, one row per budget month. A reference table (AGENTS.md): settings.zip carries it.
--   mode:          month, quarter or year (the last budget month of a quarter or of the year that starts at the year_start_month rule)
--   steps:         what the household recorded on the way: {"ack": {step: when}, "skipped": {step: when}, "viewed": {step: when}}, and on
--                  completion "done_steps", the steps the check-in had then, so a step added by a later release does not appear on it
--   completed_at:  the first time Done was pressed; reopening keeps it (reopened_at says the check-in is open again)
CREATE TABLE IF NOT EXISTS checkins (
  month         CHAR(7) PRIMARY KEY CHECK (month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  mode          TEXT NOT NULL DEFAULT 'month' CHECK (mode IN ('month', 'quarter', 'year')),
  steps         JSONB NOT NULL DEFAULT '{}'::jsonb,
  completed_at  TIMESTAMPTZ,
  completed_by  TEXT,
  reopened_at   TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
