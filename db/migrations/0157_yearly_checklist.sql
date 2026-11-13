-- #157: the annual check-in's yearly checklist. Two reference tables (AGENTS.md): settings.zip carries both as yearly_checklist.json
-- and the seed loader reads them back.
--   yearly_checklist_items  the household's list, editable on Settings → Yearly checklist; seven defaults ship, each editable or
--                           removable. `takes_amount`: the item records a signed amount (the tax outcome, #153).
--   yearly_checklist        a tick per budget year (the year's label, the year it starts in) and item, with a note. The item's label is
--                           kept as it stood when ticked, so a later rename or removal leaves past years as they were.
CREATE TABLE IF NOT EXISTS yearly_checklist_items (
  key           TEXT PRIMARY KEY CHECK (key ~ '^[a-z0-9_]+$'),
  label         TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  position      INTEGER NOT NULL DEFAULT 0,
  takes_amount  BOOLEAN NOT NULL DEFAULT false,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO yearly_checklist_items (key, label, position, takes_amount) VALUES
  ('insurance', 'Review insurance renewals and coverage', 10, false),
  ('beneficiaries', 'Check beneficiaries on retirement and insurance accounts', 20, false),
  ('credit_reports', 'Pull credit reports', 30, false),
  ('subscriptions', 'Audit subscriptions (the Recurring tab)', 40, false),
  ('goals', 'Review goals and set next year''s targets', 50, false),
  ('tax_outcome', 'Record last year''s tax outcome', 60, true),
  ('yearly_bills', 'Confirm the yearly bills list', 70, false)
ON CONFLICT (key) DO NOTHING;
CREATE TABLE IF NOT EXISTS yearly_checklist (
  year     CHAR(4) NOT NULL CHECK (year ~ '^\d{4}$'),
  item     TEXT NOT NULL,
  label    TEXT NOT NULL DEFAULT '',
  note     TEXT NOT NULL DEFAULT '',
  done_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  done_by  TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (year, item)
);
