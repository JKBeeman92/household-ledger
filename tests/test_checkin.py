"""The monthly check-in (#164): a page per month whose steps come from a registry, Done only when no step is to do, catch-up over
missing months, the checkin.completed webhook, a step added later not appearing on a done check-in, and the CLI's status."""
from __future__ import annotations
import json

import pytest

from conftest import GOLDEN, upload_files
from app import admin, checkin, db, hooks


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")   # 2025-01 … 2025-04; the fixture seeds accounts with balances up to 2025-03


def ov(month):
    with db.pool.connection() as conn:
        return checkin.overview(conn, month)


def status(month):
    return {s["key"]: s["status"] for s in ov(month)["steps"]}


def finish_latest(client, month="2025-04"):
    """Confirm the open balances and goals, read the standing and coming-up steps: what a household does at the sit-down."""
    o = ov(month)
    bal = next(s for s in o["steps"] if s["key"] == "balances")["context"]["bal"]["rows"]
    client.post(f"/balances/{month}", data={"account": [r["name"] for r in bal], "amount": [f"{r['value']:.2f}" for r in bal]})
    with db.pool.connection() as conn:
        conn.execute("UPDATE goals SET value=coalesce(value, 1), note='', confirmed_at=now()"); conn.commit()
    client.post(f"/checkin/{month}/steps/standing/view")
    client.post(f"/checkin/{month}/steps/coming_up/view")
    client.post(f"/checkin/{month}/steps/next/ack")   # no changes for next month (#155)


def test_modes_follow_the_year_start_rule(client, reset_db):
    with db.pool.connection() as conn:
        assert [checkin.mode_of(conn, m) for m in ("2025-03", "2025-06", "2025-11", "2025-12")] == ["quarter", "quarter", "month", "year"]
        db.put_setting(conn, "rules", {"year_start_month": 7})
        assert [checkin.mode_of(conn, m) for m in ("2025-06", "2025-09", "2025-12", "2025-07")] == ["year", "quarter", "quarter", "month"]


def test_steps_registry_and_statuses(client, loaded):
    st = status("2025-04")
    assert list(st) == ["files", "books", "balances", "goals", "standing", "coming_up", "review", "next"]   # 4 (decisions) only once one is in force
    assert st["files"] == "done" and st["books"] == "done" and st["balances"] == "todo" and st["standing"] == "todo" and st["coming_up"] == "todo"
    assert st["review"] == "skipped"   # no review provider: optional
    page = client.get("/checkin/2025-04").text
    assert "April 2025" in page and 'id="step-balances"' in page and "disabled" in page.split('id="checkin-complete"')[1]
    assert client.get("/checkin").headers["location"] == "/checkin/2025-04"


def test_done_is_refused_until_no_step_is_to_do_then_fires_the_webhook(client, loaded, monkeypatch):
    sent = []
    monkeypatch.setattr(hooks, "notify_async", lambda conn, event, month=None, **kw: sent.append((event, month, kw)))
    r = client.post("/checkin/2025-04/complete")
    assert r.status_code == 303 and not ov("2025-04")["done"] and not sent
    finish_latest(client)
    assert ov("2025-04")["can_complete"], status("2025-04")
    client.post("/checkin/2025-04/complete")
    o = ov("2025-04")
    assert o["done"] and o["row"]["completed_by"] and sent == [("checkin.completed", "2025-04", {"mode": "month", "steps": {s["key"]: s["status"] for s in o["steps"]}})]
    first = o["row"]["completed_at"]
    client.post("/checkin/2025-04/reopen")
    assert not ov("2025-04")["done"]
    client.post("/checkin/2025-04/complete")
    assert ov("2025-04")["done"] and ov("2025-04")["row"]["completed_at"] == first   # reopening keeps the first completion time


def test_a_step_that_does_not_apply_never_blocks_and_a_later_step_is_not_retroactive(client, loaded, monkeypatch):
    finish_latest(client); client.post("/checkin/2025-04/complete")
    assert ov("2025-04")["done"]
    late = checkin.Step("decisions", 90, "Next month", lambda conn, m, s: ("todo", "pick up to three"))
    monkeypatch.setitem(checkin.REGISTRY, "decisions", late)   # as #155 will register it
    assert "decisions" not in status("2025-04")                 # done before it existed
    assert "decisions" in [s["key"] for s in ov("2025-05")["steps"]]   # a month not yet done gets it
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM accounts WHERE name NOT IN (SELECT account FROM balances)"); conn.execute("TRUNCATE balances, balance_history"); conn.execute("DELETE FROM accounts"); conn.commit()
    assert "balances" not in status("2025-05")   # no accounts: the step does not apply


def test_catch_up_over_three_missing_months(client, loaded):
    o = ov("2025-04")
    assert o["missing"] == ["2025-01", "2025-02", "2025-03"]
    assert "January, February and March have no check-in" in client.get("/checkin/2025-04").text
    for m in o["missing"]:
        short = ov(m)
        assert short["short"] and [s["key"] for s in short["steps"]] == ["files", "books", "balances"]   # the short form
        bal = next(s for s in short["steps"] if s["key"] == "balances")["context"]["bal"]["rows"]
        assert any(r["value"] is None for r in bal) or m != "2025-01"   # an account first valued later is optional in an earlier month
        client.post(f"/balances/{m}", data={"account": [r["name"] for r in bal], "amount": ["" if r["value"] is None else f"{r['value']:.2f}" for r in bal]})
        r = client.post(f"/checkin/{m}/complete")
        assert ov(m)["done"], status(m)
        nxt = {"2025-01": "/checkin/2025-02", "2025-02": "/checkin/2025-03", "2025-03": "/checkin/2025-04"}[m]
        assert r.headers["location"] == nxt   # on to the next month to catch up, then this month's full check-in
    assert ov("2025-04")["missing"] == []


def test_acknowledging_problems_and_skipping_the_review(client, loaded):
    with db.pool.connection() as conn:   # a budget figure that no longer matches its transactions
        conn.execute("UPDATE budget_items SET spent = spent + 1 WHERE budget_month='2025-04' AND item='Groceries'"); conn.commit()
    assert status("2025-04")["books"] == "attention"
    client.post("/checkin/2025-04/steps/books/ack")
    assert status("2025-04")["books"] == "done"
    assert client.post("/checkin/2025-04/steps/files/skip").status_code == 400   # Files cannot be skipped


def test_last_month_line_and_cli(client, loaded, capsys):
    page = client.get("/").text
    assert "April 2025 check-in:" in page and 'href="/checkin/2025-04">continue' in page and "3 earlier months without one" in page
    assert admin.main(["checkin", "status", "2025-04"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("2025-04 check-in (month): ") and "Balances" in out and "Earlier months without a check-in: 2025-01, 2025-02, 2025-03" in out
    finish_latest(client); client.post("/checkin/2025-04/complete")
    assert admin.main(["checkin", "status"]) == 0 and "done" in capsys.readouterr().out
    assert "done" in client.get("/").text.split('id="lm-checkin"')[1][:200]


def test_settings_zip_carries_done_check_ins(client, loaded, reset_db, tmp_path):
    import io, zipfile
    finish_latest(client); client.post("/checkin/2025-04/complete")
    zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content)).extractall(tmp_path)
    assert json.loads((tmp_path / "checkins.json").read_text())[0]["month"] == "2025-04"
    reset_db(tmp_path)
    with db.pool.connection() as conn:
        assert conn.execute("SELECT month, completed_at IS NOT NULL AS done FROM checkins").fetchall() == [{"month": "2025-04", "done": True}]
