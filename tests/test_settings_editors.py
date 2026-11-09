"""Settings editors: row-level saves for mapping, goals and events, line-numbered CSV errors, CSV export."""
from __future__ import annotations
import csv, dataclasses, io

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
