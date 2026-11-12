-- Issue #143: balances once a month, for debts and assets. New tables only; nothing existing changes.
--   accounts:        a reference table (settings.zip, the seed loader): what the household owes and owns, a handful of rows.
--   balances:        a raw table (sources.zip): month-end balances typed by a person. They are facts, never derived; a month with
--                    no row is estimated (a debt) or carried (an asset) by the build, which never writes here.
--   balance_history: the value a correction or a deletion replaced, as source_file_history keeps replaced files (AGENTS.md).

CREATE TABLE IF NOT EXISTS accounts (
  name          TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('debt', 'asset')),
  -- a debt's type is a debt type (#141; empty: the linked canonical item's); an asset's is one of the asset types
  type          TEXT CHECK (type IS NULL OR (kind = 'debt' AND type IN ('mortgage', 'student', 'auto', 'card', 'medical', 'personal', 'other'))
                                           OR (kind = 'asset' AND type IN ('cash', 'savings', 'retirement', 'brokerage', 'home', 'vehicle', 'other'))),
  person        TEXT,                -- one of the people rule (#109); NULL is joint
  debt_item     TEXT REFERENCES canonical_items(name) ON UPDATE CASCADE ON DELETE SET NULL,   -- the canonical item its payments go to
  apr           NUMERIC(8,6) CHECK (apr IS NULL OR (apr >= 0 AND apr <= 1)),                 -- a fraction: 0.065 is 6.5%
  escrow        NUMERIC(14,2) CHECK (escrow IS NULL OR escrow >= 0),                          -- the part of a monthly payment that is not the loan
  emergency     BOOLEAN NOT NULL DEFAULT false,   -- counts toward emergency-fund coverage (#150)
  in_net_worth  BOOLEAN NOT NULL DEFAULT true,
  active        BOOLEAN NOT NULL DEFAULT true,    -- retired accounts keep their balances and get no new ones
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (kind = 'debt' OR (debt_item IS NULL AND apr IS NULL AND escrow IS NULL))
);

CREATE TABLE IF NOT EXISTS balances (
  account       TEXT NOT NULL REFERENCES accounts(name) ON UPDATE CASCADE,   -- no ON DELETE: an account with balances is retired, not deleted
  budget_month  CHAR(7) NOT NULL CHECK (budget_month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  amount        NUMERIC(14,2) NOT NULL,   -- what is owed (a debt) or held (an asset) at the end of the budget month
  source        TEXT NOT NULL CHECK (source IN ('entered', 'carried', 'estimated')),   -- stored rows are 'entered' or 'carried' (a suggestion accepted)
  entered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  entered_by    TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (account, budget_month)
);

CREATE TABLE IF NOT EXISTS balance_history (
  id            BIGSERIAL PRIMARY KEY,
  account       TEXT NOT NULL,            -- not a foreign key: history outlives renames as text, like renames.old
  budget_month  CHAR(7) NOT NULL,
  amount        NUMERIC(14,2) NOT NULL,
  source        TEXT NOT NULL,
  entered_at    TIMESTAMPTZ NOT NULL,
  entered_by    TEXT NOT NULL DEFAULT '',
  event         TEXT NOT NULL CHECK (event IN ('replaced', 'deleted')),
  event_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_by      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS balance_history_idx ON balance_history (account, budget_month, event_at DESC);
