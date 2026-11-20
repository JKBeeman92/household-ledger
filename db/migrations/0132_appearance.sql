-- #132: one appearance system. A household's choice of colour, and whether to hide it from the charts, per name in a dimension:
-- a category, a life-event kind, an employer, a person, a debt, an account type of net worth, or a catch-all item. A name with no
-- row draws exactly as before (its place in the palette). Display only: the pipeline never reads it, so no rebuild follows a change.
-- A reference table (AGENTS.md): settings.zip carries it as appearance.json and the seed loader reads it back; later migrations
-- must carry its data. A category or canonical rename (#133) renames its rows in the same transaction (app/renames.py).
--   color   'series-1' … 'series-8' (a palette slot: follows the kit, per mode) or a CSS colour (fixed in both modes); NULL: the palette
--   hidden  out of that dimension's charts (bars, lines, legend); totals, net lines, tables and reconciliation never change
CREATE TABLE IF NOT EXISTS appearance (
  dimension  TEXT NOT NULL CHECK (dimension IN ('category','event_kind','employer','person','debt','account_type','item')),
  name       TEXT NOT NULL,
  color      TEXT NULL,
  hidden     BOOLEAN NOT NULL DEFAULT false,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (dimension, name)
);
