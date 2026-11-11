"""python -m app.admin (#108): the Settings editors' own functions from a shell, with the same validation and exit codes a script can act on."""
from __future__ import annotations
import json, logging

import pytest

from conftest import GOLDEN, upload_files
from test_settings_editors import _banner_shown
from app import admin, db


def run(capsys, *argv) -> tuple[int, str, str]:
    code = admin.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def mapping(conn):
    return {r["item"]: dict(r) for r in conn.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping").fetchall()}


def test_export_then_import_mapping_round_trips(client, reset_db, capsys, tmp_path):
    code, csv_text, _ = run(capsys, "export-mapping")
    assert code == 0 and csv_text.splitlines()[0] == "Item,CanonicalItem,Category,Flow,Note"
    with db.pool.connection() as conn:
        before = mapping(conn)
    f = tmp_path / "m.csv"; f.write_text(csv_text.replace("Rent,Rent,Housing,expense,", "Rent,Bag End Rent,Housing,expense,"))
    code, out, _ = run(capsys, "import-mapping", str(f))
    assert code == 0 and f"Saved {len(before)} mapping rows." in out and "rebuild" in out
    with db.pool.connection() as conn:
        assert mapping(conn)["Rent"]["canonical_item"] == "Bag End Rent"
    assert _banner_shown(client.get("/settings").text)   # marked as changed, like a Settings save: the Rebuild now banner shows


def test_refused_input_says_why_exits_1_and_changes_nothing(client, reset_db, capsys, tmp_path):
    with db.pool.connection() as conn:
        before = (mapping(conn), conn.execute("SELECT count(*) AS n FROM life_events").fetchone()["n"])
    bad = tmp_path / "bad.csv"; bad.write_text("Item,CanonicalItem,Category,Flow,Note\nRent,Rent,Housing,bogus,\n")
    code, out, err = run(capsys, "import-mapping", str(bad))
    assert code == 1 and out == "" and "Not saved — 1 problem: line 2: bad Flow on: Rent" in err
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps([{"m": "3019-03", "label": "Ring destroyed"}, {"m": "TA 3019", "label": "x"}]))
    code, _, err = run(capsys, "set-events", str(ev))
    assert code == 1 and "item 2: m month must look like YYYY-MM" in err
    rules = tmp_path / "r.json"; rules.write_text(json.dumps({"employer": []}))
    code, _, err = run(capsys, "set-rules", str(rules))
    assert code == 1 and "employer" in err
    with db.pool.connection() as conn:
        assert (mapping(conn), conn.execute("SELECT count(*) AS n FROM life_events").fetchone()["n"]) == before


def test_set_events_goals_rules_and_export(client, reset_db, capsys, tmp_path):
    ev = tmp_path / "ev.json"; ev.write_text(json.dumps([{"m": "2025-03", "label": "Moved to Crickhollow"}]))
    assert run(capsys, "set-events", str(ev))[0] == 0
    code, out, _ = run(capsys, "export", "events")
    assert code == 0 and json.loads(out) == [{"m": "2025-03", "label": "Moved to Crickhollow"}]
    g = tmp_path / "g.json"; g.write_text(json.dumps([{"key": "giving_share", "label": "Giving", "value": "7%", "unit": "share of income", "note": ""}]))
    assert run(capsys, "set-goals", str(g))[0] == 0
    r = tmp_path / "r.json"; r.write_text(json.dumps({"people": ["Rosie", "Sam"]}))
    assert run(capsys, "set-rules", str(r))[0] == 0
    with db.pool.connection() as conn:
        assert float(conn.execute("SELECT value FROM goals WHERE key='giving_share'").fetchone()["value"]) == 0.07
        assert db.get_setting(conn, "rules", {})["people"] == ["Rosie", "Sam"]


def test_writes_are_logged_with_the_command_and_user(client, reset_db, capsys, tmp_path, caplog):
    ev = tmp_path / "ev.json"; ev.write_text("[]")
    with caplog.at_level(logging.INFO, logger="ledger.admin"):
        run(capsys, "set-events", str(ev))
    assert any(r.name == "ledger.admin" and r.getMessage().startswith("set-events by ") and "(cli)" in r.getMessage() for r in caplog.records)


def test_draft_then_add_mapping(client, reset_db, capsys, tmp_path):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM item_mapping WHERE item='Groceries'"); conn.commit()
    upload_files(client, GOLDEN / "exports")
    code, draft, err = run(capsys, "draft-mapping")
    assert code == 0 and "Groceries,Groceries,Food,expense,drafted from group Food" in draft and "add-mapping" in err
    f = tmp_path / "d.csv"; f.write_text(draft)
    code, out, _ = run(capsys, "add-mapping", str(f))
    assert code == 0 and "Saved 1 drafted mapping row." in out
    assert run(capsys, "add-mapping", str(f))[0] == 1   # a second time: already mapped, refused
    code, _, err = run(capsys, "draft-mapping")
    assert code == 0 and "already has a mapping row" in err


def test_rebuild_exit_codes(client, reset_db, capsys, monkeypatch):
    upload_files(client, GOLDEN / "exports")
    code, out, _ = run(capsys, "rebuild")
    assert code == 0 and "reconcile" in out
    from pipeline import run_pipeline
    with db.pool.connection() as holder:   # another rebuild holds the lock
        holder.execute("SELECT pg_advisory_lock(%s, %s)", run_pipeline.BUILD_LOCK)
        code, _, err = run(capsys, "rebuild")
        holder.execute("SELECT pg_advisory_unlock(%s, %s)", run_pipeline.BUILD_LOCK); holder.commit()
    assert code == admin.EX_TEMPFAIL == 75 and "already running" in err
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM item_mapping WHERE item='Rent'"); conn.commit()
    code, _, err = run(capsys, "rebuild")
    assert code == 1 and "Rebuild failed" in err and "Rent" in err


def test_usage_error_is_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        admin.main(["set-everything", "x"])
    assert e.value.code == 2
