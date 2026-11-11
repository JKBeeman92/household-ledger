"""Settings editors: row-level saves for mapping, goals and events, line-numbered CSV errors, CSV export."""
from __future__ import annotations
import csv, dataclasses, io, json

import pytest

from app import db


@pytest.fixture
def auth_on(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))


def mapping(conn):
    return {r["item"]: dict(r) for r in conn.execute("SELECT item, canonical_item, category, flow, note FROM item_mapping").fetchall()}


ROW = {"item": "Bywater Rent", "canonical_item": "Rent", "category": "Housing", "flow": "expense", "note": "x"}


def test_row_add_edit_rename_delete_leave_other_rows_alone(client, reset_db):
    with db.pool.connection() as conn:
        before = mapping(conn)
    r = client.post("/settings/mapping/row", data=ROW)
    assert r.status_code == 200 and r.json()["ok"]
    r = client.post("/settings/mapping/row", data={**ROW, "original": "Bywater Rent", "flow": "debt", "note": "y"})
    assert r.status_code == 200
    r = client.post("/settings/mapping/row", data={**ROW, "original": "Bywater Rent", "item": "  Bywater   Rent 2 "})
    assert r.json()["row"]["item"] == "Bywater Rent 2"
    with db.pool.connection() as conn:
        now = mapping(conn)
    assert set(now) - set(before) == {"Bywater Rent 2"} and now["Bywater Rent 2"]["flow"] == "expense"
    assert {k: v for k, v in now.items() if k != "Bywater Rent 2"} == before
    assert client.post("/settings/mapping/row/delete", data={"original": "Bywater Rent 2"}).json()["deleted"] == 1
    assert client.post("/settings/mapping/row/delete", data={"original": "Bywater Rent 2"}).status_code == 404
    with db.pool.connection() as conn:
        assert mapping(conn) == before


def test_bad_row_names_the_field_and_changes_nothing(client, reset_db):
    with db.pool.connection() as conn:
        before = mapping(conn)
    r = client.post("/settings/mapping/row", data={**ROW, "flow": "bogus", "category": ""})
    assert r.status_code == 200 and not r.json()["ok"] and set(r.json()["errors"]) == {"flow", "category"} and "bogus" in r.json()["errors"]["flow"]
    first = next(iter(before))
    r = client.post("/settings/mapping/row", data={**ROW, "item": first})  # add colliding with an existing item
    assert r.status_code == 200 and not r.json()["ok"] and "already" in r.json()["errors"]["item"]
    with db.pool.connection() as conn:
        assert mapping(conn) == before


def test_csv_import_error_names_the_line_and_keeps_table(client, reset_db):
    with db.pool.connection() as conn:
        before = mapping(conn)
    body = "Item,CanonicalItem,Category,Flow,Note\nRent,Rent,Housing,expense,\nTea,Tea,Food,bogus,\nRent,Rent,Housing,expense,\n"
    page = client.get(client.post("/settings/mapping", data={"body": body}).headers["location"]).text
    assert "Not saved" in page and "line 3: bad Flow on: Tea" in page and "line 4: item duplicate of line 2" in page
    with db.pool.connection() as conn:
        assert mapping(conn) == before


def test_csv_import_replaces_and_export_round_trips(client, reset_db):
    body = "Item,CanonicalItem,Category,Flow,Note\r\nRent,Rent,Housing,expense,\r\nPaycheck,Paycheck,Income,income,\"a, b\"\r\n"
    assert client.post("/settings/mapping", data={"body": body}).status_code == 303
    with db.pool.connection() as conn:
        assert set(mapping(conn)) == {"Rent", "Paycheck"}
    r = client.get("/settings/mapping.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert {x["Item"]: x["Note"] for x in rows} == {"Rent": "", "Paycheck": "a, b"}


def test_large_csv_import(client, reset_db):
    body = "Item,CanonicalItem,Category,Flow,Note\n" + "".join(f"Item {i},Canon {i % 40},Cat {i % 9},expense,\n" for i in range(250))
    client.post("/settings/mapping", data={"body": body})
    with db.pool.connection() as conn:
        assert len(mapping(conn)) == 250


def test_goal_rows(client, reset_db):
    g = {"key": "second_breakfast", "label": "Second breakfast fund", "value": "12.5", "unit": "usd", "note": ""}
    assert client.post("/settings/goals/row", data=g).json()["row"]["value"] == 12.5
    assert client.post("/settings/goals/row", data={**g, "original": "second_breakfast", "value": ""}).json()["row"]["value"] is None
    r = client.post("/settings/goals/row", data={**g, "value": "lots", "label": ""})
    assert r.status_code == 200 and not r.json()["ok"] and set(r.json()["errors"]) == {"value", "label"}
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM goals WHERE key='second_breakfast' AND value IS NULL").fetchone()["n"] == 1
    assert client.post("/settings/goals/row/delete", data={"original": "second_breakfast"}).json()["ok"]


def test_event_rows(client, reset_db):
    e = {"m": "2026-04", "label": "Pippin visits"}
    assert client.post("/settings/events/row", data=e).status_code == 200
    r = client.post("/settings/events/row", data={"m": "2026-13", "label": ""})
    assert r.status_code == 200 and not r.json()["ok"] and set(r.json()["errors"]) == {"m", "label"}
    assert not client.post("/settings/events/row", data=e).json()["ok"]  # duplicate add
    r = client.post("/settings/events/row", data={"m": "2026-05", "label": "Pippin visits", "original_m": "2026-04", "original_label": "Pippin visits"})
    assert r.status_code == 200
    with db.pool.connection() as conn:
        assert [x["month"] for x in conn.execute("SELECT month FROM life_events WHERE label='Pippin visits'").fetchall()] == ["2026-05"]
    assert client.post("/settings/events/row/delete", data={"original_m": "2026-05", "original_label": "Pippin visits"}).json()["deleted"] == 1


def test_bulk_json_goals_validated_before_replacing(client, reset_db):
    with db.pool.connection() as conn:
        n = conn.execute("SELECT count(*) n FROM goals").fetchone()["n"]
    page = client.get(client.post("/settings/goals", data={"body": '[{"key":"a","label":"A","value":"x"},{"label":"B"}]'}).headers["location"]).text
    assert "Not saved" in page and "item 1: value" in page and "item 2: key" in page
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM goals").fetchone()["n"] == n


def test_unknown_row_kind_and_page_renders_structured_tables(client, reset_db):
    assert client.post("/settings/nope/row", data={}).status_code == 404
    assert client.post("/settings/cpi/row/delete", data={}).status_code == 404
    page = client.get("/settings").text
    for kind in ("mapping", "goals", "events"):
        assert f'data-kind="{kind}"' in page
    assert "/static/settings.js" in page and "/settings/mapping.csv" in page


def test_row_endpoints_need_sign_in(client, reset_db, auth_on):
    assert client.post("/settings/mapping/row", data=ROW).status_code == 401
    assert client.post("/settings/mapping/row/delete", data={"original": "x"}).status_code == 401
    assert client.get("/settings/mapping.csv").status_code == 307


def test_renaming_a_goal_keeps_its_confirmation(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM goals WHERE key IN ('ren_a','ren_b')")
        conn.execute("INSERT INTO goals (key,value,unit,label,note,confirmed_at) VALUES ('ren_a',5,'usd','Ren','',now())")
        conn.commit()
        stamp = conn.execute("SELECT confirmed_at FROM goals WHERE key='ren_a'").fetchone()["confirmed_at"]
    r = client.post("/settings/goals/row", data={"original": "ren_a", "key": "ren_b", "label": "Ren", "value": "6", "unit": "usd", "note": ""})
    assert r.json()["ok"]
    with db.pool.connection() as conn:
        assert conn.execute("SELECT 1 FROM goals WHERE key='ren_a'").fetchone() is None
        g = conn.execute("SELECT value, confirmed_at FROM goals WHERE key='ren_b'").fetchone()
    assert g["confirmed_at"] == stamp and float(g["value"]) == 6


def _cpi(conn):
    return conn.execute("SELECT month, cpi, source FROM cpi ORDER BY month").fetchall()


@pytest.mark.parametrize("body,needle", [
    ("month,cpi,source\n", "no CPI rows"),
    ("when,value\n2026-01,300\n", "header"),
    ("month,cpi,source\n2026-01,300,x\n2026-1,301,x\n", "line 3: month must look like YYYY-MM"),
    ("month,cpi,source\n2026-01,abc,x\n", "line 2: cpi must be a positive number"),
    ("month,cpi,source\n2026-01,-4,x\n", "line 2: cpi must be a positive number"),
    ("month,cpi,source\n2026-01,300,x\n2026-02,301,x\n2026-01,302,x\n", "line 4: month 2026-01 repeats line 2"),
])
def test_bulk_cpi_is_validated_before_anything_is_deleted(client, reset_db, body, needle):
    with db.pool.connection() as conn:
        before = _cpi(conn)
    assert before
    page = client.get(client.post("/settings/cpi", data={"body": body}).headers["location"]).text
    assert "Not saved" in page and needle in page
    with db.pool.connection() as conn:
        assert _cpi(conn) == before


def test_bulk_cpi_valid_save_replaces_the_table(client, reset_db):
    with db.pool.connection() as conn:
        db.put_setting(conn, "rules", {}); conn.commit()   # the golden rules name a base month that this smaller table drops
    body = "month,cpi,source\n2026-01,300.5,mine\n2026-02,301,\n"
    page = client.get(client.post("/settings/cpi", data={"body": body}).headers["location"]).text
    assert "Saved 2 CPI months" in page
    with db.pool.connection() as conn:
        assert [(r["month"], float(r["cpi"]), r["source"]) for r in _cpi(conn)] == [("2026-01", 300.5, "mine"), ("2026-02", 301.0, "")]


def test_rules_cpi_base_month_must_be_in_the_cpi_table(client, reset_db):
    with db.pool.connection() as conn:
        rules_before = db.get_setting(conn, "rules", {})
        have = conn.execute("SELECT month FROM cpi ORDER BY month LIMIT 1").fetchone()["month"]
    page = client.get(client.post("/settings/rules", data={"body": json.dumps({"cpi_base_month": "1999-01"})}).headers["location"]).text
    assert "Not saved" in page and "1999-01 is not a month in the CPI table" in page
    with db.pool.connection() as conn:
        assert db.get_setting(conn, "rules", {}) == rules_before
    page = client.get(client.post("/settings/rules", data={"body": json.dumps({"cpi_base_month": have})}).headers["location"]).text
    assert "Saved rules" in page


def test_cpi_save_cannot_drop_the_month_the_rules_name(client, reset_db):
    with db.pool.connection() as conn:
        base = conn.execute("SELECT month FROM cpi ORDER BY month LIMIT 1").fetchone()["month"]
        db.put_setting(conn, "rules", {"cpi_base_month": base}); conn.commit()
        before = _cpi(conn)
    others = "".join(f"{r['month']},{r['cpi']},{r['source']}\n" for r in before if r["month"] != base)
    page = client.get(client.post("/settings/cpi", data={"body": "month,cpi,source\n" + others}).headers["location"]).text
    assert "Not saved" in page and base in page and "cpi_base_month" in page
    with db.pool.connection() as conn:
        assert _cpi(conn) == before


@pytest.mark.parametrize("raw, unit, value, ok", [
    ("0.07", "share of gross", 0.07, True), ("7%", "share of gross", 0.07, True), ("7", "share of gross", None, False),
    ("1.5", "share of take-home", None, False), ("78,000", "usd", 78000.0, True), ("", "share of gross", None, True), ("lots", "usd", None, False)])
def test_goal_values_are_checked_against_their_unit(raw, unit, value, ok):
    from app.routes.settings import parse_goal_value
    v, problem = parse_goal_value(raw, unit)
    assert (problem is None) is ok and (v == value or (v is not None and abs(v - value) < 1e-12))


def test_a_share_typed_as_a_whole_number_is_refused_in_both_editors(client, reset_db):
    r = client.post("/settings/goals/row", data={"key": "giving_target", "label": "Giving target", "value": "7", "unit": "share of gross", "note": ""}).json()
    assert r["ok"] is False and "write 7%" in r["errors"]["value"]
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO goals (key, value, unit, label, note) VALUES ('giving_target', 0.07, 'share of gross', 'Giving target', '') ON CONFLICT (key) DO UPDATE SET value=0.07"); conn.commit()
    page = client.get(client.post("/goals/giving_target/confirm", data={"value": "7"}).headers["location"]).text
    assert "a share is between 0 and 1" in page
    client.post("/goals/giving_target/confirm", data={"value": "8%"})
    with db.pool.connection() as conn:
        assert float(conn.execute("SELECT value FROM goals WHERE key='giving_target'").fetchone()["value"]) == 0.08


def _banner_shown(page: str) -> bool:
    i = page.find('id="rebuild-banner"')
    return i != -1 and "hidden" not in page[i:page.find(">", i)]


def test_a_saved_goal_says_a_rebuild_is_needed_until_one_has_run(client, reset_db):
    """The dashboard reads only what a build wrote: a saved goal must say so, offer the rebuild, and stop saying so after it."""
    from conftest import GOLDEN, upload_files
    upload_files(client, GOLDEN / "exports")   # the test client runs the background rebuild before returning
    assert not _banner_shown(client.get("/").text)
    g = {"key": "second_breakfast", "label": "Second breakfast fund", "value": "12.5", "unit": "usd", "note": ""}
    assert client.post("/settings/goals/row", data=g).json()["rebuild_needed"] is True
    for path in ("/", "/settings", "/upload"):
        page = client.get(path).text
        assert _banner_shown(page) and 'action="/rebuild"' in page, path
    client.post("/rebuild")
    assert not _banner_shown(client.get("/").text)


def test_a_rejected_row_and_the_household_editor_do_not_ask_for_a_rebuild(client, reset_db):
    assert client.post("/settings/goals/row", data={"key": "", "label": ""}).json()["rebuild_needed"] is False
    client.post("/settings/household", data={"body": json.dumps({"name": "Bagshot Row", "title": "Ledger"})})
    with db.pool.connection() as conn:
        assert db.rebuild_needed(conn) is False
