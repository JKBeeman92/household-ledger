-- What migration 0133 (issue #133) would do to this database's item mapping, without changing anything.
-- Run against a 0.4.x database (or a copy of one) before upgrading:
--   docker compose exec -T db psql -U ledger ledger < db/preview/0133_mapping_preview.sql
-- or, with the 0.5.0 image and DATABASE_URL pointing at the copy: python -m app.admin preview-mapping-migration
-- Each row is one change; no rows means the migration keeps everything as it is.
WITH votes AS (
  SELECT canonical_item, category, count(*) AS n,
         row_number() OVER (PARTITION BY canonical_item ORDER BY count(*) DESC, category) AS rk
  FROM item_mapping WHERE category <> 'UNMAPPED' GROUP BY canonical_item, category
)
SELECT 'UNMAPPED placeholder' AS change, item AS name,
       'its category was the placeholder UNMAPPED, so it leaves the mapping; it is listed as unmapped until you map it' AS detail
FROM item_mapping WHERE category = 'UNMAPPED'
UNION ALL
SELECT 'category conflict', k.canonical_item,
       k.category || ' (' || k.n || ' row' || CASE WHEN k.n = 1 THEN '' ELSE 's' END || ') kept; '
       || string_agg(d.category || ' (' || d.n || ' row' || CASE WHEN d.n = 1 THEN '' ELSE 's' END || ')', ', ' ORDER BY d.n DESC, d.category)
       || ' dropped'
FROM votes k JOIN votes d ON d.canonical_item = k.canonical_item AND d.rk > 1
WHERE k.rk = 1
GROUP BY k.canonical_item, k.category, k.n
ORDER BY 1, 2;
