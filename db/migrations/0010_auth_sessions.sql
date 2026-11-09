-- #10: the server-side half of a sign-in. The signed session cookie holds a random id; this row, keyed by its sha256, holds the
-- OIDC tokens that do not fit in the cookie: id_token (logout's id_token_hint) and the access/refresh tokens for the group re-check.
-- Ephemeral: deleting every row only signs everyone out.
CREATE TABLE IF NOT EXISTS auth_sessions (
  id_hash           TEXT PRIMARY KEY,
  sub               TEXT NOT NULL,
  email             TEXT NOT NULL DEFAULT '',
  id_token          TEXT,
  access_token      TEXT,
  access_expires_at BIGINT,                      -- epoch seconds, as the token response gives it
  refresh_token     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  checked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),   -- last group re-check (or sign-in)
  expires_at        TIMESTAMPTZ NOT NULL                  -- sign-in + SESSION_MAX_AGE; never extended
);
CREATE INDEX IF NOT EXISTS auth_sessions_expires_at ON auth_sessions (expires_at);
