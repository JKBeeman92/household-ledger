-- What migration 0133 (issue #133) would do to this database's item mapping, without changing anything.
-- Run against a 0.4.x database (or a copy of one) before upgrading:
--   docker compose exec -T db psql -U ledger ledger < db/preview/0133_mapping_preview.sql
-- or, with the 0.5.0 image and DATABASE_URL pointing at the copy: python -m app.admin preview-mapping-migration
-- Each row is one change, in the order the migration makes them; no rows means it keeps the mapping exactly as it is.
-- Keep in step with db/migrations/0133_categories_and_canonical_items.sql (tests/test_definitions.py compares the two).
WITH raw AS (
  SELECT item, canonical_item AS c0, category AS g0,
         btrim(regexp_replace(translate(coalesce(canonical_item, ''), E'    \t\r\n', '       '), ' {2,}', ' ', 'g')) AS c1,
         btrim(regexp_replace(translate(coalesce(category, ''), E'    \t\r\n', '       '), ' {2,}', ' ', 'g')) AS g1
  FROM item_mapping
), kept AS (
  SELECT item, CASE WHEN c1 = '' THEN item ELSE c1 END AS canonical_item, g1 AS category FROM raw WHERE g1 <> '' AND upper(g1) <> 'UNMAPPED'
), votes AS (
  SELECT canonical_item, category, count(*) AS n,
         row_number() OVER (PARTITION BY canonical_item ORDER BY count(*) DESC, category) AS rk
  FROM kept GROUP BY canonical_item, category
)
SELECT change, name, detail FROM (
  SELECT 1 AS step, 'spacing tidied' AS change, item AS name,
         concat_ws(' and ', CASE WHEN c0 <> c1 THEN 'canonical item becomes "' || c1 || '"' END,
                            CASE WHEN g0 <> g1 THEN 'category becomes "' || g1 || '"' END) AS detail
  FROM raw WHERE (c0 <> c1 OR g0 <> g1) AND g1 <> '' AND upper(g1) <> 'UNMAPPED'
  UNION ALL
  SELECT 2, 'leaves the mapping', item,
         'its category is ' || CASE WHEN g1 = '' THEN 'blank' ELSE 'the placeholder ' || g1 END || '; it is listed as unmapped until you map it'
  FROM raw WHERE g1 = '' OR upper(g1) = 'UNMAPPED'
  UNION ALL
  SELECT 3, 'canonical item filled in', item, 'its canonical item is blank, so it becomes ' || item
  FROM raw WHERE c1 = '' AND g1 <> '' AND upper(g1) <> 'UNMAPPED'
  UNION ALL
  SELECT 4, 'category conflict', k.canonical_item,
         k.category || ' (' || k.n || ' row' || CASE WHEN k.n = 1 THEN '' ELSE 's' END || ') kept; '
         || string_agg(d.category || ' (' || d.n || ' row' || CASE WHEN d.n = 1 THEN '' ELSE 's' END || ')', ', ' ORDER BY d.n DESC, d.category)
         || ' dropped'
  FROM votes k JOIN votes d ON d.canonical_item = k.canonical_item AND d.rk > 1
  WHERE k.rk = 1
  GROUP BY k.canonical_item, k.category, k.n
) changes
ORDER BY step, name;
