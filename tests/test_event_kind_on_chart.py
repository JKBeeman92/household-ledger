"""One way off the charts for a life event (issue #235): Chart appearance's hide switch for event kinds is retired; a kind keeps its
colour, an event leaves the charts by its On the chart switch (#140), and the chips stay each viewer's filter. Migration 0235 turns
every hidden kind into its events' on_chart = false (and the stored events dataset with them), so the chart is the same after the
upgrade; an older appearance.json seeds the same way; the Settings save, set-appearance and the table itself refuse a hidden kind.
The charts themselves are in test_event_timeline.py and test_appearance.py (browser)."""
from __future__ import annotations
import json, shutil

import psycopg
import pytest

from test_migrations import scratch, q   # noqa: F401  (fixture)
from app import admin, appearance, db
from pipeline.run_pipeline import seed_reference_tables

EVENTS = [("2025-01", "New job", "job", True), ("2025-02", "Knee", "health", True), ("2025-03", "Ankle", "health", False),
          ("2025-04", "Moved", "move", True), ("2025-05", "Baby", "family", True), ("2025-06", "Plain", None, True)]


def drawn_before(events, hidden_kinds):
    """What a timeline drew before 0.9.0's #235 for a viewer who never pressed "show": on the chart AND of a kind not hidden."""
    return sorted(label for _, label, kind, on in events if on and kind not in hidden_kinds)


def test_the_migration_turns_a_hidden_kind_into_its_events_off_the_chart_and_the_chart_is_the_same(scratch, tmp_path):  # noqa: F811
    before = tmp_path / "before"; before.mkdir()
    for p in sorted(db.MIGRATIONS.glob("*.sql")):
        if p.name < "0235":
            shutil.copy(p, before / p.name)
    db.migrate(scratch, before)
    ds = [{"m": m, "label": label, **({"kind": k} if k else {}), **({} if on else {"on_chart": False})} for m, label, k, on in EVENTS]
    with psycopg.connect(scratch, autocommit=True) as c:
        c.cursor().executemany("INSERT INTO life_events (month, label, kind, on_chart) VALUES (%s, %s, %s, %s)", EVENTS)
        c.execute("INSERT INTO appearance (dimension, name, color, hidden) VALUES ('event_kind', 'health', NULL, true), "
                  "('event_kind', 'move', 'series-2', true), ('event_kind', 'family', 'series-5', false), ('category', 'Food', NULL, true)")
        c.execute("INSERT INTO datasets (name, rows, row_count) VALUES ('events', %s, %s), ('monthly', '[]', 0)", (json.dumps(ds), len(ds)))
    old = drawn_before(EVENTS, {"health", "move"})
    assert old == ["Baby", "New job", "Plain"]

    assert db.migrate(scratch) == [p.name for p in sorted(db.MIGRATIONS.glob("*.sql")) if p.name >= "0235"]
    after = q(scratch, "SELECT month, label, kind, on_chart FROM life_events ORDER BY month")
    assert sorted(label for _, label, _, on in after if on) == old   # the household sees the same chart
    assert [r[:3] for r in after] == [e[:3] for e in EVENTS]   # nothing else about an event changes
    (rows,), = q(scratch, "SELECT rows FROM datasets WHERE name = 'events'")
    assert sorted(r["label"] for r in rows if r.get("on_chart") is not False) == old   # and the page, before the next build
    assert [r["label"] for r in rows] == [e[1] for e in EVENTS] and {k for r in rows for k in r} <= {"m", "label", "kind", "on_chart"}
    assert q(scratch, "SELECT rows FROM datasets WHERE name = 'monthly'") == [([],)]
    # the kinds keep their colour; a kind that held only the flag loses its row; other dimensions keep their hide switch
    assert q(scratch, "SELECT dimension, name, color, hidden FROM appearance ORDER BY dimension, name") == [
        ("category", "Food", None, True), ("event_kind", "family", "series-5", False), ("event_kind", "move", "series-2", False)]
    with psycopg.connect(scratch, autocommit=True) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute("UPDATE appearance SET hidden = true WHERE dimension = 'event_kind'")
    assert db.migrate(scratch) == []


def test_the_migration_leaves_a_household_that_hid_no_kind_alone(scratch, tmp_path):  # noqa: F811
    before = tmp_path / "before"; before.mkdir()
    for p in sorted(db.MIGRATIONS.glob("*.sql")):
        if p.name < "0235":
            shutil.copy(p, before / p.name)
    db.migrate(scratch, before)
    with psycopg.connect(scratch, autocommit=True) as c:
        c.cursor().executemany("INSERT INTO life_events (month, label, kind, on_chart) VALUES (%s, %s, %s, %s)", EVENTS)
        c.execute("INSERT INTO datasets (name, rows, row_count) VALUES ('events', '[{\"m\": \"2025-01\", \"label\": \"New job\"}]', 1)")
    db.migrate(scratch)
    assert [r[3] for r in q(scratch, "SELECT month, label, kind, on_chart FROM life_events ORDER BY month")] == [e[3] for e in EVENTS]
    assert q(scratch, "SELECT rows FROM datasets WHERE name = 'events'") == [([{"m": "2025-01", "label": "New job"}],)]


# ------------------------------------------------------------------ an older appearance.json, and the ways in that refuse it

OLD = [{"dimension": "event_kind", "name": "health", "hidden": True}, {"dimension": "event_kind", "name": "move", "color": "series-2", "hidden": True},
       {"dimension": "category", "name": "Food", "hidden": True}]


def test_an_older_appearance_json_that_hid_a_kind_seeds_as_its_events_off_the_chart(client, reset_db, tmp_path):
    (tmp_path / "events.json").write_text(json.dumps([{"m": m, "label": label, **({"kind": k} if k else {}), **({} if on else {"on_chart": False})}
                                                      for m, label, k, on in EVENTS]))
    (tmp_path / "appearance.json").write_text(json.dumps(OLD))
    with db.pool.connection() as conn:   # a new install, from a settings.zip made before #235
        conn.execute("DELETE FROM life_events"); conn.execute("DELETE FROM appearance")
        conn.execute("DELETE FROM settings WHERE key = ANY(%s)", ([appearance.WRITTEN, db.INPUTS_CHANGED],))
        seed_reference_tables(conn, tmp_path); conn.commit()
        on = {r["label"]: r["on_chart"] for r in conn.execute("SELECT label, on_chart FROM life_events").fetchall()}
        assert sorted(k for k, v in on.items() if v) == drawn_before(EVENTS, {"health", "move"})
        assert [(r["dimension"], r["name"], r["color"], r["hidden"]) for r in appearance.rows(conn)] == [
            ("category", "Food", None, True), ("event_kind", "move", "series-2", False)]   # the colour stays; a flag-only row is no row
        assert db.rebuild_needed(conn)   # the events changed, so the next build carries them into the events dataset
        exported = json.loads(appearance.export_json(conn))
    assert not [x for x in exported if x["dimension"] == "event_kind" and x.get("hidden")]   # what settings.zip writes now


def test_the_settings_save_refuses_a_hidden_kind_and_shows_no_hide_box(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM appearance"); conn.commit()
    r = client.post("/settings/appearance/save", data={"dimension": "event_kind", "name": ["health", "move"], "color": ["", "series-2"],
                                                       "hidden": ["health"]}, follow_redirects=True)
    assert "Not saved: health: a life-event kind has no hide switch; switch its events off the chart with On the chart in Settings → Life events" in r.text
    with db.pool.connection() as conn:
        assert appearance.rows(conn) == []   # nothing written, not even the colour
        with pytest.raises(ValueError, match="no hide switch"):
            appearance.set_one(conn, "event_kind", "move", "series-2", True)
        conn.rollback()
    client.post("/settings/appearance/save", data={"dimension": "event_kind", "name": ["health", "move"], "color": ["", "series-2"]})
    with db.pool.connection() as conn:
        assert [(r["name"], r["color"], r["hidden"]) for r in appearance.rows(conn)] == [("move", "series-2", False)]


def test_set_appearance_refuses_a_hidden_kind_with_a_message_naming_on_the_chart(client, reset_db, tmp_path, capsys):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM appearance"); appearance.set_one(conn, "category", "Food", "series-3", False); conn.commit()
    f = tmp_path / "appearance.json"; f.write_text(json.dumps(OLD))
    assert admin.main(["set-appearance", str(f)]) == 1
    err = capsys.readouterr().err
    assert "item 1: event_kind health: a life-event kind has no hide switch" in err and "On the chart in Settings → Life events" in err
    assert "item 2: event_kind move" in err and "item 3" not in err   # a hidden category is still fine
    with db.pool.connection() as conn:
        assert [(r["name"], r["color"]) for r in appearance.rows(conn)] == [("Food", "series-3")]   # refused, nothing changed
    with pytest.raises(ValueError, match="item 1: event_kind health"):
        appearance.parse(OLD)
    kinds: list[str] = []
    assert appearance.parse(OLD, converted=kinds) == [{"dimension": "event_kind", "name": "move", "color": "series-2", "hidden": False},
                                                      {"dimension": "category", "name": "Food", "color": None, "hidden": True}]
    assert kinds == ["health", "move"]


def test_the_dashboard_never_asks_whether_an_event_kind_is_hidden():
    """The markers, the lists and the "N hidden · show" notes of the timeline and Paycheck source read on_chart and the chips only."""
    from conftest import ROOT
    page = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "dash.hidden('event_kind'" not in page and "shown(chart, 'event_kind'" not in page
    assert "hidden kind'" not in page and "evHiddenKinds" not in page and "hiddenAlso" not in page
    assert "HL.hiddenNote(el, 'monthly'" not in page   # the timeline's only hidden names were event kinds
    assert page.count("dash.assigned('event_kind', e.kind)") == 1   # the colour still works


def test_a_kind_kept_for_a_name_the_data_lost_is_listed_as_a_colour_only(client, reset_db):
    from conftest import GOLDEN, upload_files
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM appearance")
        appearance.set_one(conn, "event_kind", "Gone kind", "series-3", False); conn.commit()
        assert [o["name"] for o in appearance.page(conn)["orphans"]] == ["Gone kind"]
    html = client.get("/settings").text
    table = html[html.index('id="appearance-orphans-table"'):]; table = table[:table.index("</table>")]
    assert "<td>No hide switch</td>" in table and "<td>no</td>" not in table and "<td>—</td>" not in table   # said in words, not a bare dash
    assert 'aria-label="Delete the colour kept for the life-event kind Gone kind"' in table and "and hide switch" not in table
    assert '<span class="swatch" style="--sw: var(--series-3)" aria-hidden="true"></span> series-3' in table   # the colour it holds, seen
    r = client.post("/settings/appearance/delete", data={"dimension": "event_kind", "name": "Gone kind"})
    assert r.status_code == 303 and r.headers["location"] == "/settings#appearance"   # none left: no Not in the data tab to land on
    assert "Deleted the colour kept for Gone kind; if Gone kind comes back, it is automatic again." in client.get("/settings").text
