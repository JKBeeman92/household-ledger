"""Month-close checklist: goals with no value or an 'ask' note get a prompt; confirming records the date and reaches the goals dataset."""
from __future__ import annotations

import pytest

from conftest import GOLDEN, upload_files
from app import db

GOALS = [("gross_annual", 78000, "usd", "Gross salary, annual", "Ask at every close."),
         ("pay_frequency", 26, "paychecks/yr", "Pay frequency", "Biweekly; a task for HR is not a prompt."),
         ("investing", None, "usd", "Investing per paycheck", ""),
         ("giving_floor", 0.05, "share of gross", "Giving floor", "Asked once a year")]


@pytest.fixture
def goals(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM goals")
        for g in GOALS:
            conn.execute("INSERT INTO goals (key,value,unit,label,note) VALUES (%s,%s,%s,%s,%s)", g)
    return client


def row(key):
    with db.pool.connection() as conn:
        return conn.execute("SELECT value, confirmed_at FROM goals WHERE key=%s", (key,)).fetchone()


def test_only_null_or_ask_goals_get_a_prompt(goals):
    html = goals.get("/upload").text
    assert 'action="/goals/gross_annual/confirm"' in html and 'action="/goals/investing/confirm"' in html and 'action="/goals/giving_floor/confirm"' in html
    assert "/goals/pay_frequency/confirm" not in html
    assert "never confirmed" in html


def test_no_checklist_when_nothing_to_ask(client, reset_db):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM goals"); conn.execute("INSERT INTO goals (key,value,unit,label,note) VALUES ('a',1,'','A','')")
    assert "Check these before you close a month" not in client.get("/upload").text


def test_confirm_unchanged_records_date_only(goals):
    r = goals.post("/goals/gross_annual/confirm", data={"value": "78000"})
    assert r.status_code == 303 and "confirmed unchanged" in goals.get(r.headers["location"]).text
    g = row("gross_annual")
    assert g["value"] == 78000 and g["confirmed_at"] is not None


def test_update_changes_value_and_fills_null(goals):
    goals.post("/goals/gross_annual/confirm", data={"value": "81000"})
    goals.post("/goals/investing/confirm", data={"value": "150"})
    assert row("gross_annual")["value"] == 81000 and row("investing")["value"] == 150 and row("investing")["confirmed_at"]


@pytest.mark.parametrize("bad", ["abc", "nan", "1e99"])
def test_bad_number_or_empty_null_is_rejected(goals, bad):
    goals.post("/goals/investing/confirm", data={"value": bad})
    assert row("investing")["confirmed_at"] is None and row("investing")["value"] is None
    goals.post("/goals/investing/confirm", data={"value": ""})
    assert row("investing")["confirmed_at"] is None


def test_unknown_goal_is_flashed(goals):
    r = goals.post("/goals/nope/confirm", data={"value": "1"})
    assert r.status_code == 303 and "No goal" in goals.get(r.headers["location"]).text


def test_goals_dataset_carries_confirmed_at(goals):
    goals.post("/goals/investing/confirm", data={"value": "150"})
    upload_files(goals, GOLDEN / "exports")
    ds = {r["key"]: r for r in goals.get("/api/dataset/goals").json()}
    assert ds["investing"]["confirmed_at"][:4].isdigit() and ds["gross_annual"]["confirmed_at"] is None


def test_settings_editor_keeps_confirmations(goals):
    goals.post("/goals/gross_annual/confirm", data={"value": "78000"})
    before = row("gross_annual")["confirmed_at"]
    from app.routes.settings import _save_editor, _editor_body
    with db.pool.connection() as conn:
        _save_editor(conn, "goals", _editor_body(conn, "goals")); conn.commit()
    assert row("gross_annual")["confirmed_at"] == before
