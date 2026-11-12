-- Issue #133: categories and canonical items defined once, so a rename is one edit.
-- item_mapping.category is replaced by canonical_items.category; categories and canonical items are keyed by name with
-- ON UPDATE CASCADE, so a rename is one UPDATE and every query that reads names keeps working.
-- Data (AGENTS.md: reference tables migrate their data), in this order, each case noted in mapping_migration_notes, which Settings
-- shows until dismissed; db/preview/0133_mapping_preview.sql (python -m app.admin preview-mapping-migration) reports the same
-- without changing anything:
--   1. names are tidied as the app tidies them on input (no-break and other odd spaces become spaces, runs of spaces become one,
--      ends trimmed), so "Rent " and "Rent" are one canonical item;
--   2. a row whose category is blank or the placeholder UNMAPPED (in any case) leaves the mapping: the build lists it as unmapped;
--   3. a row with a blank canonical item takes its item's name;
--   4. a canonical item mapped under more than one category keeps the one most of its rows use (ties: alphabetical).

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

-- 1. tidy names (the same rule as pipeline/run_pipeline.norm)
CREATE FUNCTION pg_temp.tidy(t TEXT) RETURNS TEXT LANGUAGE SQL IMMUTABLE AS
$$ SELECT btrim(regexp_replace(translate(coalesce(t, ''), E'    \t\r\n', '       '), ' {2,}', ' ', 'g')) $$;

INSERT INTO mapping_migration_notes (note)
SELECT item || ': spacing tidied in ' || concat_ws(' and ',
         CASE WHEN canonical_item <> pg_temp.tidy(canonical_item) THEN 'its canonical item (now "' || pg_temp.tidy(canonical_item) || '")' END,
         CASE WHEN category <> pg_temp.tidy(category) THEN 'its category (now "' || pg_temp.tidy(category) || '")' END)
FROM item_mapping
WHERE (canonical_item <> pg_temp.tidy(canonical_item) OR category <> pg_temp.tidy(category))
  AND pg_temp.tidy(category) <> '' AND upper(pg_temp.tidy(category)) <> 'UNMAPPED'
ORDER BY item;
UPDATE item_mapping SET canonical_item = pg_temp.tidy(canonical_item), category = pg_temp.tidy(category)
WHERE canonical_item <> pg_temp.tidy(canonical_item) OR category <> pg_temp.tidy(category);

-- 2. no category: out of the mapping
INSERT INTO mapping_migration_notes (note)
SELECT item || ': its category was ' || CASE WHEN category = '' THEN 'blank' ELSE 'the placeholder ' || category END
       || ', so it left the mapping; it is listed as unmapped until you map it'
FROM item_mapping WHERE category = '' OR upper(category) = 'UNMAPPED' ORDER BY item;
DELETE FROM item_mapping WHERE category = '' OR upper(category) = 'UNMAPPED';

-- 3. no canonical item: the item's own name
INSERT INTO mapping_migration_notes (note)
SELECT item || ': its canonical item was blank, so it is now ' || item FROM item_mapping WHERE canonical_item = '' ORDER BY item;
UPDATE item_mapping SET canonical_item = item WHERE canonical_item = '';

-- 4. one category per canonical item
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
  name  TEXT PRIMARY KEY CHECK (btrim(name) <> '' AND upper(name) <> 'UNMAPPED')
);
CREATE TABLE IF NOT EXISTS canonical_items (
  name      TEXT PRIMARY KEY CHECK (btrim(name) <> ''),
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
