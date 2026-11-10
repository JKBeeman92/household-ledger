-- Personal API tokens (#34) for scripts, monitoring and agents. Only the sha256 of the random token is kept; the token itself
-- is shown once, at creation. A revoked row is kept (revoked_at set) so the list of what existed stays auditable.
CREATE TABLE IF NOT EXISTS api_tokens (
  id            BIGSERIAL PRIMARY KEY,
  token_hash    TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  scope         TEXT NOT NULL CHECK (scope IN ('read','read+review')),
  owner_email   TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ,
  revoked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS api_tokens_owner_idx ON api_tokens (owner_email, created_at DESC);
