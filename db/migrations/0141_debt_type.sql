-- Issue #141: a debt type on the canonical item, for the Debts tab's groups and, later, payoff estimates (#144) and the housing
-- ratio (#145). NULL behaves as before: an untyped debt is drawn as it always was. A new type later is an additive change here.
ALTER TABLE canonical_items
  ADD COLUMN IF NOT EXISTS debt_type TEXT CHECK (debt_type IN ('mortgage', 'student', 'auto', 'card', 'medical', 'personal', 'other'));

-- the mapping view gains the column at its end (CREATE OR REPLACE VIEW may only append)
CREATE OR REPLACE VIEW mapping_rows AS
SELECT m.item, m.canonical_item, c.category, m.flow, m.note, m.updated_at, c.debt_type
FROM item_mapping m JOIN canonical_items c ON c.name = m.canonical_item;
