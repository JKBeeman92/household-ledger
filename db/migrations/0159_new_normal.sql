-- Issue #159: any life event can be marked "the new normal" (a move, a new dependent, a new job): averages used elsewhere (the look
-- ahead's pace, the emergency fund's outgo, the suggested plan) then start from the latest such event with at least three months
-- loaded, and say so. Always the household's choice: existing rows stay false, nothing is ticked for them.
-- life_events is a reference table: the column travels in settings.zip's events.json and is read back by the seed loader.
ALTER TABLE life_events ADD COLUMN IF NOT EXISTS new_normal BOOLEAN NOT NULL DEFAULT false;
