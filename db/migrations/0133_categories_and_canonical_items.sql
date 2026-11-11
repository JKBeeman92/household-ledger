-- Issue #133: categories and canonical items defined once, so a rename is one edit.
-- item_mapping.category is replaced by canonical_items.category; categories and canonical items are keyed by name with
-- ON UPDATE CASCADE, so a rename is one UPDATE and every query that reads names keeps working.
-- Data (AGENTS.md: reference tables migrate their data):
--   * a canonical item mapped under more than one category keeps the category most of its rows use (ties: alphabetical);
--     each such case is recorded in mapping_migration_notes, which Settings shows until dismissed;
--   * rows with the placeholder category UNMAPPED leave the mapping (UNMAPPED is a build finding, not a category), and are noted too.
-- db/preview/0133_mapping_preview.sql (python -m app.admin preview-mapping-migration) reports the same, without changing anything.

CREATE TABLE IF NOT EXISTS mapping_migration_notes (
  id            BIGSERIAL PRIMARY KEY,
  note          TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  dismissed_at  TIMESTAMPTZ
);

-- what was renamed, by whom and when; stored reviews keep their text, and the Reviews page notes later renames on them
CREATE TABLE IF NOT EXISTS renames (
  id         BIGSERIAL PRIMARY KEY,
  at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  dimension  TEXT NOT NULL CHECK (dimension IN ('category', 'canonical_item')),
  old        TEXT NOT NULL,
  new        TEXT NOT NULL,
  by         TEXT NOT NULL DEFAULT ''
);

INSERT INTO mapping_migration_notes (note)
SELECT item || ': its category was the placeholder UNMAPPED, so it left the mapping; it is listed as unmapped until you map it'
FROM item_mapping WHERE category = 'UNMAPPED' ORDER BY item;
DELETE FROM item_mapping WHERE category = 'UNMAPPED';

CREATE TEMP TABLE _canon_votes ON COMMIT DROP AS
SELECT canonical_item, category, count(*) AS n,
       row_number() OVER (PARTITION BY canonical_item ORDER BY count(*) DESC, category) AS rk
FROM item_mapping GROUP BY canonical_item, category;

INSERT INTO mapping_migration_notes (note)
SELECT k.canonical_item || ': ' || k.category || ' (' || k.n || ' row' || CASE WHEN k.n = 1 THEN '' ELSE 's' END || ') kept; '
       || string_agg(d.category || ' (' || d.n || ' row' || CASE WHEN d.n = 1 THEN '' ELSE 's' END || ')', ', ' ORDER BY d.n DESC, d.category)
       || ' dropped'
FROM _canon_votes k JOIN _canon_votes d ON d.canonical_item = k.canonical_item AND d.rk > 1
WHERE k.rk = 1
GROUP BY k.canonical_item, k.category, k.n
ORDER BY k.canonical_item;

CREATE TABLE IF NOT EXISTS categories (
  name  TEXT PRIMARY KEY CHECK (name <> '' AND name <> 'UNMAPPED')
);
CREATE TABLE IF NOT EXISTS canonical_items (
  name      TEXT PRIMARY KEY CHECK (name <> ''),
  category  TEXT NOT NULL REFERENCES categories (name) ON UPDATE CASCADE
);

INSERT INTO categories (name) SELECT DISTINCT category FROM _canon_votes WHERE rk = 1;
INSERT INTO canonical_items (name, category) SELECT canonical_item, category FROM _canon_votes WHERE rk = 1;

ALTER TABLE item_mapping
  ADD CONSTRAINT item_mapping_canonical_item_fkey FOREIGN KEY (canonical_item) REFERENCES canonical_items (name) ON UPDATE CASCADE,
  DROP COLUMN category;

-- the mapping as one row per item, as the CSV, the pipeline and every reader see it
CREATE VIEW mapping_rows AS
SELECT m.item, m.canonical_item, c.category, m.flow, m.note, m.updated_at
FROM item_mapping m JOIN canonical_items c ON c.name = m.canonical_item;
