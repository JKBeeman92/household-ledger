-- #206: a dismissed yearly bill remembers what it was before, so Restore can put a bill the household added (or confirmed) back
-- with its month, amount, cadence and funding, instead of deleting it. NULL: the bill came from the detector (or was dismissed
-- before this migration), and Restore hands it back to the detector as before.
ALTER TABLE yearly_bills ADD COLUMN IF NOT EXISTS dismissed_from TEXT NULL CHECK (dismissed_from IS NULL OR dismissed_from IN ('added', 'confirmed'));
