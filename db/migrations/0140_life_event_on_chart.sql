-- Issue #140: a per-event "on the chart" switch. An event can stay in the list under the chart, the review context, the check-in and
-- MCP without a marker on the timelines (for example "Started a new budget method"). Every existing event keeps drawing as before:
-- the column is NOT NULL DEFAULT true, so the ALTER sets it on every row already there (life_events is a reference table: its data
-- is carried, never dropped). Display only, like #132's appearance table: no total, table or reconciliation reads it.
-- How it combines with #132: an event is drawn only when it is on the chart AND its kind is not hidden (Settings → Chart appearance)
-- or the viewer pressed that chart's "show". "show" reveals hidden kinds, never an event switched off here.
ALTER TABLE life_events ADD COLUMN IF NOT EXISTS on_chart BOOLEAN NOT NULL DEFAULT true;
