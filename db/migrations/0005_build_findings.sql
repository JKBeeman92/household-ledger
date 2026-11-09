-- What a build found, kept with the build: {"unmapped": [...], "reconciliation": {"counts", "problems", "clean"}}.
-- The Close-a-month page reads the newest finished build's findings, so they survive page loads and never ride in a cookie.
ALTER TABLE build_log ADD COLUMN IF NOT EXISTS findings JSONB;
