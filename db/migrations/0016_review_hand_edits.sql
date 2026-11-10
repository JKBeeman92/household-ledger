-- Set when someone corrects a stored review by hand; cleared when the review is regenerated.
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS hand_edited_at TIMESTAMPTZ;
