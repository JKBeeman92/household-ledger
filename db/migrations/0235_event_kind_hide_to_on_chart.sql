-- Issue #235: a life event leaves the charts one way for the household, its On the chart switch (#140, life_events.on_chart).
-- The hide switch for event kinds in Chart appearance (#132) is retired; a kind keeps its colour there. Each kind that was hidden
-- becomes on_chart = false on that kind's events, so a household that had hidden a kind sees the same chart after the upgrade
-- (a viewer's "show" no longer brings them back: switch an event on again in Settings -> Life events). Then the kind's hide flag
-- is cleared, and a row left with nothing (no colour, not hidden) goes, as a cleared name loses its row (app/appearance.py).
-- life_events and appearance are reference tables (AGENTS.md): their data is carried. The `events` dataset is derived; its rows are
-- patched the same way so the dashboard draws the same chart before the next build rewrites it.
UPDATE life_events e SET on_chart = false
  FROM appearance a
 WHERE a.dimension = 'event_kind' AND a.hidden AND e.kind = a.name AND e.on_chart;

UPDATE datasets d SET rows = (
    SELECT coalesce(jsonb_agg(CASE WHEN x.e->>'kind' IN (SELECT name FROM appearance WHERE dimension = 'event_kind' AND hidden)
                                   THEN x.e || '{"on_chart": false}'::jsonb ELSE x.e END ORDER BY x.i), '[]'::jsonb)
      FROM jsonb_array_elements(d.rows) WITH ORDINALITY AS x(e, i))
 WHERE d.name = 'events' AND jsonb_typeof(d.rows) = 'array'
   AND EXISTS (SELECT 1 FROM appearance WHERE dimension = 'event_kind' AND hidden);

UPDATE appearance SET hidden = false, updated_at = now() WHERE dimension = 'event_kind' AND hidden;
DELETE FROM appearance WHERE dimension = 'event_kind' AND color IS NULL AND NOT hidden;

-- from now on a kind is never hidden: the Settings save, set-appearance and the seed loader say so first (app/appearance.py)
ALTER TABLE appearance DROP CONSTRAINT IF EXISTS appearance_event_kind_never_hidden;
ALTER TABLE appearance ADD CONSTRAINT appearance_event_kind_never_hidden CHECK (dimension <> 'event_kind' OR NOT hidden);
