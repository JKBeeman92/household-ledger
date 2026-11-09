-- #11: the groups from the last sign-in or group re-check, kept on the server-side row so the role (editor vs viewer) is decided
-- here and not by whichever copy of the signed cookie the browser sends. Rows from before this column have NULL, which the app
-- treats as a re-check due now (and as no editor rights until it succeeds).
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS groups TEXT[];
