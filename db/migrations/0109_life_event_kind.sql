-- Issue #109: an optional kind and person on each life event, so the timeline can group or filter them.
-- Existing rows keep NULL in both and draw exactly as before; no backfill (a kind guessed from a label would often be wrong).
-- The primary key stays (month, label). A new kind later is an additive change to this CHECK.
ALTER TABLE life_events
  ADD COLUMN IF NOT EXISTS kind TEXT CHECK (kind IN ('job', 'move', 'family', 'health', 'purchase', 'debt', 'other')),
  ADD COLUMN IF NOT EXISTS person TEXT;
