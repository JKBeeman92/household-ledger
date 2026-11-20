"""Life events on the timeline (issue #140): a per-event "on the chart" switch (migration 0140) that travels every way a life event
does (Settings, the JSON editor, the CLI, settings.zip, the seed loader, the pipeline standalone and the sample household); and, on the
page, merged markers that each list their events and take keyboard focus, chips by kind and person, and a Coming up strip after the
last loaded month.

How the per-event switch and #132's per-kind hide combine: both are display only, and an event is drawn only when it is on the chart
AND its kind is not hidden (or the viewer pressed that chart's "show"). "show" reveals hidden kinds, never an event switched off."""
from __future__ import annotations
import json, re, shutil

import psycopg
import pytest

from conftest import GOLDEN, ROOT, SAMPLE, upload_files
from test_migrations import scratch, q   # noqa: F401  (fixture)
from test_rules_defaults import build, ds
from test_appearance import browser, harness, sample, server, set_table   # noqa: F401  (fixtures: the app in a process, the sample loaded)
from app import admin, db, review, settings_export
from pipeline.run_pipeline import rebuild, seed_reference_tables

PAGE = (ROOT / "app" / "web" / "templates" / "dashboard.html").read_text()


def events(conn):
    return [dict(r) for r in conn.execute("SELECT month, label, kind, person, on_chart FROM life_events ORDER BY month, label").fetchall()]


# ------------------------------------------------------------------ the column and its ways in and out

def test_the_migration_puts_every_existing_event_on_the_chart(scratch, tmp_path):  # noqa: F811
    before = tmp_path / "before"; before.mkdir()
    for p in sorted(db.MIGRATIONS.glob("*.sql")):
        if p.name < "0140":
            shutil.copy(p, before / p.name)
    db.migrate(scratch, before)
    with psycopg.connect(scratch, autocommit=True) as c:
        c.execute("INSERT INTO life_events (month, label, kind, person) VALUES ('2025-03', 'New job', 'job', 'Sam'), ('2025-04', 'Plain', NULL, NULL)")
    assert db.migrate(scratch) == [p.name for p in sorted(db.MIGRATIONS.glob("*.sql")) if p.name >= "0140"]
    assert q(scratch, "SELECT month, label, kind, person, on_chart FROM life_events ORDER BY month") == [
        ("2025-03", "New job", "job", "Sam", True), ("2025-04", "Plain", None, None, True)]   # kind and person kept; every event still drawn
    with psycopg.connect(scratch, autocommit=True) as c:
        c.execute("INSERT INTO life_events (month, label) VALUES ('2025-05', 'Later')")
        with pytest.raises(psycopg.errors.NotNullViolation):
            c.execute("UPDATE life_events SET on_chart = NULL")
    assert q(scratch, "SELECT on_chart FROM life_events WHERE label='Later'") == [(True,)]


def test_the_row_editor_switches_an_event_off_the_chart_and_back(client, reset_db):
    r = client.post("/settings/events/row", data={"m": "2025-02", "label": "Started a new budget method", "kind": "other", "on_chart": "no"}).json()
    assert r["ok"] and r["row"]["on_chart"] == "no"
    r = client.post("/settings/events/row", data={"m": "2025-03", "label": "Moved", "kind": "move"}).json()   # no value: on, as every event before
    assert r["ok"] and r["row"]["on_chart"] == "yes"
    r = client.post("/settings/events/row", data={"m": "2025-04", "label": "x", "on_chart": "maybe"}).json()
    assert not r["ok"] and set(r["errors"]) == {"on_chart"}
    r = client.post("/settings/events/row", data={"m": "2025-02", "label": "Started a new budget method", "kind": "other", "on_chart": "yes",
                                                  "original_m": "2025-02", "original_label": "Started a new budget method"}).json()
    with db.pool.connection() as conn:
        got = {e["label"]: e["on_chart"] for e in events(conn)}
    assert got["Started a new budget method"] is True and got["Moved"] is True and "x" not in got
    page = client.get("/settings").text
    assert 'data-fields="m,label,kind,person,on_chart,new_normal"' in page and "On the chart" in page and "&#34;on_chart&#34;: [&#34;yes&#34;, &#34;no&#34;]" in page


def test_json_editor_cli_and_settings_zip_carry_the_switch_only_when_off(client, reset_db, tmp_path, capsys):
    body = [{"m": "2025-03", "label": "New job", "kind": "job", "person": "Sam"}, {"m": "2025-04", "label": "Envelopes", "kind": "other", "on_chart": False}]
    r = client.post("/settings/events", data={"body": json.dumps(body)})
    assert "Not saved" not in client.get(r.headers["location"]).text
    with db.pool.connection() as conn:
        from app.routes.settings import _editor_body
        assert json.loads(_editor_body(conn, "events")) == body   # on_chart only when off: a file from before 0.9.0 reads the same
        files = settings_export.settings_files(conn)
    assert json.loads(files["events.json"]) == body
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps([{"m": "2025-03", "label": "x", "on_chart": "sometimes"}]))
    assert admin.main(["set-events", str(bad)]) == 1 and "on_chart yes or no" in capsys.readouterr().err
    good = tmp_path / "good.json"; good.write_text(json.dumps([{"m": "2025-05", "label": "Moved", "kind": "move", "on_chart": False}, {"m": "2025-06", "label": "On"}]))
    assert admin.main(["set-events", str(good)]) == 0
    capsys.readouterr()
    assert admin.main(["export", "events"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"m": "2025-05", "label": "Moved", "kind": "move", "on_chart": False}, {"m": "2025-06", "label": "On"}]
    # settings.zip's events.json seeds a new install with the switch as it was
    with db.pool.connection() as conn:
        (tmp_path / "seed").mkdir(); (tmp_path / "seed" / "events.json").write_bytes(settings_export.settings_files(conn)["events.json"])
        conn.execute("DELETE FROM life_events")
        seed_reference_tables(conn, tmp_path / "seed"); conn.commit()
        assert [(e["label"], e["on_chart"]) for e in events(conn)] == [("Moved", False), ("On", True)]


def test_the_seed_loader_reads_the_switch_as_settings_does_and_names_a_bad_one(tmp_path, client, reset_db):
    # one rule with the JSON editor and set-events (check_on_chart): yes/no text is read, and null is on, like a missing switch
    (tmp_path / "events.json").write_text(json.dumps([{"m": "2025-01", "label": "Shire moot", "on_chart": "no"}, {"m": "2025-02", "label": "Feast", "on_chart": None}]))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events")
        seed_reference_tables(conn, tmp_path); conn.commit()
        assert [(e["label"], e["on_chart"]) for e in events(conn)] == [("Shire moot", False), ("Feast", True)]
    (tmp_path / "events.json").write_text(json.dumps([{"m": "2025-01", "label": "Shire moot", "on_chart": "sometimes"}]))
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM life_events")
        with pytest.raises(ValueError, match=r"event 2025-01 'Shire moot': on_chart must be yes or no, true or false"):
            seed_reference_tables(conn, tmp_path)
        conn.rollback()


def test_the_pipeline_passes_the_switch_through_standalone_and_from_the_database(tmp_path, client, reset_db):
    wd = build(tmp_path / "plain", {})
    assert ds(wd, "events") == json.loads((GOLDEN / "events.json").read_text())   # no switch in the file: the dataset is unchanged
    ev = [{"m": "2025-01", "label": "New job", "kind": "job"}, {"m": "2025-02", "label": "Envelopes", "on_chart": False}]
    wd2 = tmp_path / "off"; shutil.copytree(wd, wd2)
    (wd2 / "events.json").write_text(json.dumps(ev))
    from test_pipeline_golden import run_scripts
    run_scripts(wd2)
    assert ds(wd2, "events") == ev
    # from the database: events.json holds on_chart only for an event switched off, so the dataset says false or nothing
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO life_events (month, label, on_chart) VALUES ('2025-01', 'Envelopes', false)"); conn.commit()
        rebuild(conn)
        rows = conn.execute("SELECT rows FROM datasets WHERE name='events'").fetchone()["rows"]
    assert {"m": "2025-01", "label": "Envelopes", "on_chart": False} in rows and all("on_chart" not in r for r in rows if r["label"] != "Envelopes")


def test_an_event_off_the_chart_stays_in_the_review_context(client, reset_db):
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    with db.pool.connection() as conn:
        month = conn.execute("SELECT max(budget_month) m FROM source_files").fetchone()["m"]
        conn.execute("INSERT INTO life_events (month, label, kind, on_chart) VALUES (%s, 'Tried zero-based budgeting', 'other', false)", (month,)); conn.commit()
        ctx = review.build_context(conn, month)["continuity"]["life_events"][month]
    assert {"label": "Tried zero-based budgeting", "kind": "other"} in ctx


def test_the_sample_household_exercises_the_switch_and_a_merged_marker(client, reset_db):
    reset_db(SAMPLE)
    with db.pool.connection() as conn:
        got = {e["label"]: (e["month"], e["kind"], e["on_chart"]) for e in events(conn)}
    assert got["Started budgeting by envelope"] == ("2025-05", "other", False)
    assert got["Moved to a bigger hole, rent goes up"][:2] == ("2025-10", "move") and got["Sam's knee mended at the healer"][:2] == ("2025-11", "health")
    assert sum(not on for *_, on in got.values()) == 1   # the rest draw as before


# ------------------------------------------------------------------ the page's structure

def test_the_page_reads_events_and_coming_up_through_dash_only():
    assert re.search(r'<div class="ev-filter" data-draw="ev-filter" data-source="events"></div>\s*<div class="chart" data-draw="monthly"', PAGE)   # chips above the chart
    assert 'data-draw="coming-list" data-source="coming_up events"' in PAGE
    for draw in ("ev-filter", "coming-list", "monthly"):
        body = PAGE[PAGE.index(f"dash.draw('{draw}'"):]; body = body[:body.index("\n});")]
        assert "fetch(" not in body and "/api/" not in body   # dash.data()/HL.rd only (AGENTS.md)
    assert "HL.comingUp = () => {" in PAGE and "dash.data('coming_up')" in PAGE


# ------------------------------------------------------------------ the page in a browser

def apart(marks, width):
    """The markers' buttons, plot and strip together, sit side by side: none overlaps the next, and each is a whole target wide
    (24 px, WCAG 2.5.8; 44 px tall, DS-LAYOUT-2), at any width."""
    boxes = sorted(x["box"] for x in marks)
    assert all(h >= 44 - 0.5 for *_, h in boxes), (width, boxes)
    assert all(r - l >= 24 - 0.5 for l, r, _ in boxes), (width, boxes)
    assert all(b[0] >= a[1] - 0.5 for a, b in zip(boxes, boxes[1:])), (width, boxes)


CROWD = [("2025-09", f"September thing {i}") for i in range(5)] + [("2025-10", f"October thing {i}") for i in range(5)] + \
        [("2025-11", f"November thing {i}") for i in range(5)]   # 15 events in one season (the issue's "done when")


@browser
@pytest.mark.parametrize("width", [1280, 390])
def test_the_sample_draws_merged_focusable_markers_the_switch_and_the_strip(sample, width):
    set_table([])
    s = harness(sample, width=width)["before"]
    assert not s["sideways"] and not s["sidewaysOpen"]   # the event and Coming up lists wrap inside the sheet when opened
    m = s["charts"]["monthly"]
    apart(m["marks"], width)
    labels = [x["label"] for x in m["marks"] if not x["ahead"]]
    assert all(x["focusable"] for x in m["marks"])
    assert "Oct 2025 to Nov 2025, 2 life events: Moved to a bigger hole, rent goes up (move); Sam's knee mended at the healer (health · Sam)" in labels
    assert not [x for x in labels if "budgeting by envelope" in x]   # switched off the chart ...
    ev = s["lists"]["event-list"]
    assert "May 2025: Started budgeting by envelope (other) · not on the chart" in ev["items"] and ev["summary"] == "Life events in this period (6)"   # ... kept in the list
    assert not [i for i in ev["items"] if "Bywater nursery" in i]   # after the last loaded month: in Coming up, not off the end of the axis
    # the Coming up strip: dotted, after the last month; its list says the same in words
    ahead = [x["label"] for x in m["marks"] if x["ahead"]]
    assert ahead and all(x.startswith("Coming up, ") for x in ahead) and any("Elanor starts at the Bywater nursery (family)" in x for x in ahead)
    assert m["ahead"]
    cl = s["lists"]["coming-list"]
    assert cl["summary"] == "Coming up (8)" and "Sep 2026: Elanor starts at the Bywater nursery (family)" in cl["items"]
    # what the strip's 12 months do not reach stays in the list, said so, however far ahead (the student loan's payoff is years out)
    assert [i for i in cl["items"] if "after the chart" in i] == ["Sep 2027: A new cart target (planned purchase, $30,000) · after the chart’s 12 months",
                                                                  "May 2032: Lembas Student Loan paid off (est.) (payoff) · after the chart’s 12 months"]
    assert len(ahead) <= 6 and not [x for x in ahead if "new cart" in x or "paid off" in x]
    assert "Sep 2026: Q3 federal estimate (tax deadline, $1,636)" in cl["items"]
    # the chips name only the kinds and people of events on the chart (the envelope event's "other" is not one)
    assert s["chips"] == [["All", "true"], ["Job", "false"], ["Move", "false"], ["Family", "false"], ["Health", "false"],
                          ["Everyone", "true"], ["Rosie", "false"], ["Sam", "false"]]


@browser
def test_the_chips_filter_the_markers_and_the_list_and_are_remembered(sample):
    set_table([])
    s = harness(sample, params={"evf": {"person": "Sam"}})["before"]
    labels = [x["label"] for x in s["charts"]["monthly"]["marks"] if not x["ahead"]]
    assert labels == ["Mar 2025, 1 life event: Sam takes on garden contracts (job · Sam)", "Nov 2025, 1 life event: Sam's knee mended at the healer (health · Sam)"]
    assert s["lists"]["event-list"]["summary"] == "Life events in this period (2)"
    assert ["Sam", "true"] in s["chips"] and ["Everyone", "false"] in s["chips"]
    assert [k for _, k, _ in s["charts"]["employer"]["markers"]] == ["job", "job"]   # the chips are the Overview timeline's; the Employer chart keeps its rule
    t = harness(sample, params={"evf": {"kind": "family"}})["before"]
    assert [x["label"] for x in t["charts"]["monthly"]["marks"] if not x["ahead"]] == ["Feb 2026, 1 life event: Elanor is born (family)"]
    assert not [x for x in t["charts"]["monthly"]["marks"] if x["ahead"] and "Elanor" not in x["label"] and "life event" in x["label"]]
    gone = harness(sample, params={"evf": {"kind": "wedding"}})["before"]   # a remembered choice the data no longer has reads as All
    assert ["All", "true"] in gone["chips"] and len([x for x in gone["charts"]["monthly"]["marks"] if not x["ahead"]]) == 4


@browser
def test_a_hidden_kind_and_the_switch_combine_and_show_reveals_only_the_kind(sample):
    """Hide health (Settings → Chart appearance) and other: the merged Oct–Nov marker becomes the move alone; the note counts health
    only, because the one event of kind other is switched off the chart, which no "show" brings back."""
    set_table([{"dimension": "event_kind", "name": "health", "hidden": True}, {"dimension": "event_kind", "name": "other", "hidden": True}])
    r = harness(sample, press="monthly")
    s = r["before"]
    labels = [x["label"] for x in s["charts"]["monthly"]["marks"] if not x["ahead"]]
    assert "Oct 2025, 1 life event: Moved to a bigger hole, rent goes up (move)" in labels and not [x for x in labels if "knee" in x]
    assert s["charts"]["monthly"]["note"] == "1 hidden · show"
    assert "Nov 2025: Sam's knee mended at the healer (health · Sam) · hidden kind" in s["lists"]["event-list"]["items"]   # hidden from the chart, not the list
    after = [x["label"] for x in r["after"]["charts"]["monthly"]["marks"] if not x["ahead"]]
    assert any("Sam's knee mended" in x for x in after) and not [x for x in after if "envelope" in x]
    assert s["tables"] == r["after"]["tables"]   # display only
    set_table([])


@browser
def test_fifteen_events_in_one_season_draw_at_most_one_marker_a_month_each_listing_its_events(sample):
    with db.pool.connection() as conn:
        conn.cursor().executemany("INSERT INTO life_events (month, label, kind) VALUES (%s, %s, 'other')", CROWD); conn.commit()
        rebuild(conn)
    try:
        set_table([])
        for width in (1280, 390):
            s = harness(sample, width=width)["before"]
            marks = [x for x in s["charts"]["monthly"]["marks"] if not x["ahead"]]
            # the markers holding the season (at phone width a marker may start a month or two before it, taking a nearer event)
            season = [x for x in marks if " thing " in x["label"]]
            assert 1 <= len(season) <= 3, [x["label"] for x in season]   # three months: at most one marker each
            starts = [re.match(r"[A-Z][a-z]{2} \d{4}", x["label"]).group(0) for x in marks]
            assert len(starts) == len(set(starts)), starts   # no two markers in one month
            for month, label in CROWD:
                assert sum(label + " (other)" in x["label"] for x in season) == 1, label   # every event is named by exactly one marker
            assert all(x["focusable"] for x in marks)
            apart(s["charts"]["monthly"]["marks"], width)
            assert not s["sideways"]
    finally:
        with db.pool.connection() as conn:
            conn.execute("DELETE FROM life_events WHERE label LIKE '% thing %'"); conn.commit(); rebuild(conn)


@browser
def test_an_event_later_than_the_strip_stays_in_the_coming_up_list(sample):
    """The strip draws the next 12 months; an event further ahead is in no marker, so the Coming up list keeps it, said so."""
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO life_events (month, label, kind) VALUES ('2028-05', 'Sam retires from gardening', 'job')"); conn.commit()
        rebuild(conn)
    try:
        set_table([])
        s = harness(sample)["before"]
        assert not [x for x in s["charts"]["monthly"]["marks"] if "retires" in x["label"]]
        cl = s["lists"]["coming-list"]
        assert "May 2028: Sam retires from gardening (job) · after the chart’s 12 months" in cl["items"] and cl["summary"] == "Coming up (9)"
        assert not [i for i in s["lists"]["event-list"]["items"] if "retires" in i]   # in one list, not both
    finally:
        with db.pool.connection() as conn:
            conn.execute("DELETE FROM life_events WHERE label = 'Sam retires from gardening'"); conn.commit(); rebuild(conn)
