"""Optional kind and person on life events (issue #109): validated on every way in, round-tripped on every way out, NULL for
existing rows, and carried to the pipeline, the review context and MCP."""
from __future__ import annotations
import json, shutil

import pytest

from conftest import GOLDEN, SAMPLE, upload_files
from test_rules_defaults import build, ds
from app import admin, db, review


def events(conn):
    return [dict(r) for r in conn.execute("SELECT month, label, kind, person FROM life_events ORDER BY month, label").fetchall()]


def test_existing_events_keep_null_kind_and_person(client, reset_db):
    with db.pool.connection() as conn:
        assert events(conn) and all(e["kind"] is None and e["person"] is None for e in events(conn))   # the golden seed has neither
        with pytest.raises(Exception, match="check"):
            conn.execute("INSERT INTO life_events (month, label, kind) VALUES ('2025-01', 'x', 'Job')")   # the CHECK is the fixed lower-case list
        conn.rollback()


def test_row_editor_takes_kind_and_person_and_warns_on_unknown_person(client, reset_db):
    client.post("/settings/rules/people", data={"people": "Rosie, Sam"})
    r = client.post("/settings/events/row", data={"m": "2026-04", "label": "Rosie joins the mill", "kind": "Job", "person": "Rosie"}).json()
    assert r["ok"] and r["row"]["kind"] == "job" and r["row"]["person"] == "Rosie" and r["warning"] == ""
    r = client.post("/settings/events/row", data={"m": "2026-05", "label": "Pippin visits", "kind": "", "person": "Pippin"}).json()
    assert r["ok"] and r["row"]["kind"] is None and "Pippin is not in the people rule" in r["warning"]
    r = client.post("/settings/events/row", data={"m": "2026-06", "label": "Party", "kind": "party"}).json()
    assert not r["ok"] and set(r["errors"]) == {"kind"}
    # editing an event's kind in place (same month and label) updates it
    r = client.post("/settings/events/row", data={"m": "2026-04", "label": "Rosie joins the mill", "kind": "other", "person": "",
                                                  "original_m": "2026-04", "original_label": "Rosie joins the mill"}).json()
    with db.pool.connection() as conn:
        got = {e["label"]: (e["kind"], e["person"]) for e in events(conn)}
    assert got["Rosie joins the mill"] == ("other", None) and got["Pippin visits"] == (None, "Pippin") and "Party" not in got
    page = client.get("/settings").text
    assert 'data-fields="m,label,kind,person"' in page and "&#34;job&#34;" in page and "&#34;Rosie&#34;" in page


def test_json_editor_and_cli_round_trip(client, reset_db, tmp_path, capsys):
    body = [{"m": "2025-03", "label": "New job", "kind": "job", "person": "Sam"}, {"m": "2025-04", "label": "Plain"}]
    r = client.post("/settings/events", data={"body": json.dumps(body)})
    assert "Not saved" not in client.get(r.headers["location"]).text
    with db.pool.connection() as conn:
        from app.routes.settings import _editor_body
        assert json.loads(_editor_body(conn, "events")) == body   # kind and person only when set
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps([{"m": "2025-03", "label": "x", "kind": "wedding"}]))
    assert admin.main(["set-events", str(bad)]) == 1
    good = tmp_path / "good.json"; good.write_text(json.dumps([{"m": "2025-05", "label": "Moved", "kind": "move"}]))
    assert admin.main(["set-events", str(good)]) == 0
    with db.pool.connection() as conn:
        assert events(conn) == [{"month": "2025-05", "label": "Moved", "kind": "move", "person": None}]


def test_seeds_carry_kind_and_person(client, reset_db):
    reset_db(SAMPLE)
    with db.pool.connection() as conn:
        got = {e["label"]: (e["kind"], e["person"]) for e in events(conn)}
    assert got["Sam takes on garden contracts"] == ("job", "Sam") and got["Elanor is born"] == ("family", None)


def test_pipeline_passes_kind_and_person_through_and_leaves_old_events_alone(tmp_path):
    wd = build(tmp_path / "plain", {})
    assert ds(wd, "events") == json.loads((GOLDEN / "events.json").read_text())   # no kind, no person: unchanged
    ev = [{"m": "2025-01", "label": "New job", "kind": "job", "person": "Rosie"}, {"m": "2025-02", "label": "Plain"}]
    wd2 = tmp_path / "kinds"; shutil.copytree(wd, wd2)
    (wd2 / "events.json").write_text(json.dumps(ev))
    from test_pipeline_golden import run_scripts
    run_scripts(wd2)
    assert ds(wd2, "events") == ev


def test_review_context_and_mcp_carry_kind_and_person(client, reset_db):
    reset_db(SAMPLE)
    upload_files(client, SAMPLE)
    with db.pool.connection() as conn:
        month = conn.execute("SELECT max(budget_month) m FROM source_files").fetchone()["m"]
        conn.execute("INSERT INTO life_events (month, label, kind, person) VALUES (%s, 'Sam starts at the Green Dragon', 'job', 'Sam')", (month,)); conn.commit()
        ctx = review.build_context(conn, month)["continuity"]["life_events"][month]
    assert {"label": "Sam starts at the Green Dragon", "kind": "job", "person": "Sam"} in ctx
    from test_mcp import call
    from test_api_tokens import make_token
    tok = make_token()
    err, summ = call(tok, "month_summary", {"m": "2026-02"})
    assert not err and summ["events"] == ["Elanor is born"] and summ["event_details"] == [{"label": "Elanor is born", "kind": "family"}]
