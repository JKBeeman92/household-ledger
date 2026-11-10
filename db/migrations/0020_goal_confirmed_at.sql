-- When the household last confirmed a goal at month close (NULL = never). Read by the dashboard's Goals tab ("last confirmed").
ALTER TABLE goals ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
