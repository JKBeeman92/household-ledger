"""Planned purchases (#158): needed per month, the pace and the verdict against a hand calculation for an envelope- and an
account-funded goal; coming_up; Settings add, change, delete; reaching a goal offers the event and, for a loan, the debt, prefilled,
and creates only what was ticked; goals.json round trip; the plain goals grid and checklist leave them out; MCP; the sample household."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import SAMPLE

_spec = importlib.util.spec_from_file_location("purchases_rules", Path(__file__).resolve().parent.parent / "pipeline" / "purchases.py")
pp = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pp)


def row(m, item, planned, actual=0.0):
    return {"m": m, "canonical": item, "planned": planned, "actual": actual}


def goal(key, amount, month, fund, kind, since=None, **kw):
    return {"key": key, "label": key.title(), "target_amount": amount, "target_month": month, "funded_by": fund, "funded_kind": kind, "since_month": since, **kw}


def test_envelope_goal_against_a_hand_calculation():
    # Pony Fund: $200 planned a month from 2026-01, $100 spent in 2026-03; $1,100 saved through June.
    rows = [row(f"2026-0{i}", "Pony Fund", 200) for i in range(1, 7)] + [row("2026-03", "Pony Fund", 0, 100)] + [row("2025-12", "Pony Fund", 999)]
    out = pp.progress([goal("pony", 3000, "2026-12", "Pony Fund", "envelope", "2026-01")], rows, "2026-06")
    r = out[0]
    assert r["saved"] == 1100.0                     # 6 × 200 − 100; December 2025 is before since_month
    assert r["months_left"] == 6                    # July .. December
    assert r["needed"] == round(1900 / 6, 2)        # 316.67
    assert r["pace"] == 200.0                       # April .. June: 600 ÷ 3 (March's spending is before the window)
    assert r["verdict"] == "short" and r["short_by"] == round(round(1900 / 6, 2) - 200, 2)
    assert r["reach_m"] == "2027-04"   # ⌈1900 ÷ 200⌉ = 10 months after June 2026
    assert pp.words(r) == "$117 a month short"


def test_account_goal_ahead_on_pace_reached_and_past():
    bal = [{"account": "Savings", "m": m, "amount": a} for m, a in (("2026-03", 4000), ("2026-04", 4300), ("2026-05", 4600), ("2026-06", 4900))]
    g = [goal("cart", 6000, "2026-12", "Savings", "account"), goal("roof", 5000, "2027-06", "Savings", "account"),
         goal("rug", 4000, "2026-09", "Savings", "account"), goal("boat", 9000, "2026-05", "Savings", "account"), goal("bike", 900, "2027-01", "Nowhere", "account")]
    out = {r["key"]: r for r in pp.progress(g, [], "2026-06", bal)}
    cart = out["cart"]   # saved is the balance; pace (4900 − 4000) ÷ 3 = 300; needed 1100 ÷ 6 = 183.33; 300 ≥ 1.1 × 183.33
    assert (cart["saved"], cart["pace"], cart["needed"], cart["verdict"]) == (4900.0, 300.0, 183.33, "ahead")
    roof = out["roof"]   # 100 ÷ 12 = 8.33 needed: ahead too; reached at the pace in a month
    assert roof["verdict"] == "ahead" and roof["reach_m"] == "2026-07"
    assert out["rug"]["verdict"] == "reached" and out["rug"]["remaining"] == 0
    boat = out["boat"]   # the month has gone: no per-month figure, the shortfall instead
    assert boat["verdict"] == "past" and boat["needed"] is None and boat["remaining"] == 4100.0 and pp.words(boat) == "past its date, $4,100 short"
    assert out["bike"]["verdict"] == "no_pace" and out["bike"]["saved"] is None   # no such account
    # coming_up: every open goal whose month is still ahead, reached or not; the boat's month has gone
    assert [c["ref"] for c in pp.coming_up_rows(list(out.values()))] == ["rug", "cart", "bike", "roof"]


def test_on_pace_and_done():
    rows = [row(f"2026-0{i}", "Fund", 100) for i in range(4, 7)]
    out = {r["key"]: r for r in pp.progress([goal("a", 900, "2026-12", "Fund", "envelope", "2026-04"),
                                             goal("b", 500, "2026-12", "Fund", "envelope", "2026-04", done_at="2026-06-30T00:00:00", done_month="2026-06")], rows, "2026-06")}
    assert out["a"]["needed"] == 100.0 and out["a"]["pace"] == 100.0 and out["a"]["verdict"] == "on_pace"
    assert out["b"]["verdict"] == "done" and pp.coming_up_rows([out["b"]]) == []
    assert pp.progress([goal("x", 1, "2026-12", "F", "envelope")], [], None) == []


# ---- through the app ----

@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def _db():
    from app import db
    return db.pool.connection()


def _ds(client, name):
    return client.get("/api/datasets").json()[name]


def test_sample_household_has_planned_purchases(client, sample):
    rows = {r["key"]: r for r in _ds(client, "planned_purchases")}
    assert set(rows) == {"purchase:a-trip-to-rivendell", "purchase:a-new-cart"}
    trip = rows["purchase:a-trip-to-rivendell"]
    assert trip["funded_kind"] == "envelope" and trip["verdict"] in ("ahead", "on_pace", "short", "reached")
    cart = rows["purchase:a-new-cart"]
    assert cart["funded_kind"] == "account" and cart["saved"] is not None
    assert {c["ref"] for c in _ds(client, "coming_up") if c["kind"] == "goal"} == {k for k, r in rows.items() if r["target_month"] > r["m"]}
    page = client.get("/checkin/2026-06").text
    assert "Planned purchases" in page and "A trip to Rivendell" in page


def test_settings_add_change_delete_and_a_failed_add_keeps_what_was_typed(client, sample):
    r = client.post("/purchases", data={"label": "Pony", "amount": "1,500", "month": "2027-03", "funded_by": "Holiday"})
    assert r.status_code == 303 and r.headers["location"] == "/settings?focus=pp-pony#goals/purchases"
    with _db() as conn:
        g = conn.execute("SELECT target_amount, target_month, funded_by, funded_kind, since_month, value FROM goals WHERE key='purchase:pony'").fetchone()
    assert (float(g["target_amount"]), g["target_month"], g["funded_by"], g["funded_kind"], g["since_month"], g["value"]) == (1500.0, "2027-03", "Holiday", "envelope", "2026-06", None)
    r = client.post("/purchases", data={"label": "Boat", "amount": "lots", "month": "2027-03", "funded_by": "Holiday"})
    assert r.headers["location"] == "/settings?focus=pp-add#goals/purchases"
    page = client.get(r.headers["location"]).text
    assert 'value="lots" aria-invalid="true"' in page and "The amount is a number" in page
    r = client.post("/purchases", data={"label": "Boat", "amount": "100", "month": "2027-03", "funded_by": "The Shire Bank"})
    assert "is not one of your budget items or accounts" in client.get(r.headers["location"]).text
    client.post("/purchases", data={"original": "purchase:pony", "label": "Pony and trap", "amount": "1800", "month": "2027-06", "funded_by": "Green Hills Savings"})
    with _db() as conn:
        g = conn.execute("SELECT label, target_amount, funded_kind FROM goals WHERE key='purchase:pony'").fetchone()
    assert (g["label"], float(g["target_amount"]), g["funded_kind"]) == ("Pony and trap", 1800.0, "account")
    page = client.get("/settings").text
    assert 'id="st-goals-purchases"' in page and 'id="pp-pony"' in page
    assert 'data-key="purchase:pony"' not in page   # not in the plain goals grid
    from app.routes.checklist import checklist_goals
    with _db() as conn:
        assert not any(g["key"].startswith("purchase:") for g in checklist_goals(conn))   # not asked at month close as a goal with no value
    client.post("/purchases/purchase:pony/delete")
    with _db() as conn:
        assert conn.execute("SELECT 1 FROM goals WHERE key='purchase:pony'").fetchone() is None


def test_bought_with_a_loan_sets_up_only_what_was_ticked(client, sample):
    client.post("/purchases", data={"label": "Cart", "amount": "100", "month": "2026-09", "funded_by": "Green Hills Savings"})
    client.post("/rebuild")
    page = client.get("/checkin/2026-06").text   # reached (the account holds far more): the check-in asks
    assert "Did you buy Cart?" in page and 'value="Cart Loan"' in page
    # nothing ticked: only marked bought
    r = client.post("/purchases/purchase:cart/bought", data={"month": "2026-06", "back": "2026-06"})
    assert r.headers["location"] == "/checkin/2026-06#pp-cart"
    with _db() as conn:
        assert conn.execute("SELECT done_month FROM goals WHERE key='purchase:cart'").fetchone()["done_month"] == "2026-06"
        assert conn.execute("SELECT 1 FROM life_events WHERE label='Bought Cart'").fetchone() is None
    client.post("/purchases/purchase:cart/reopen")
    # a bad APR: nothing is written, the field is named
    r = client.post("/purchases/purchase:cart/bought", data={"month": "2026-06", "add_event": "1", "with_loan": "1", "loan_item": "Cart Loan",
                                                               "loan_category": "Debt", "loan_type": "auto", "loan_account": "Cart Loan", "loan_balance": "2500", "loan_apr": "lots"})
    with _db() as conn:
        assert conn.execute("SELECT done_at FROM goals WHERE key='purchase:cart'").fetchone()["done_at"] is None
        assert conn.execute("SELECT 1 FROM accounts WHERE name='Cart Loan'").fetchone() is None
    client.post("/purchases/purchase:cart/bought", data={"month": "2026-06", "add_event": "1", "with_loan": "1", "loan_item": "Cart Loan",
                                                          "loan_category": "Debt", "loan_type": "auto", "loan_account": "Cart Loan", "loan_balance": "2,500", "loan_apr": "6.9%"})
    with _db() as conn:
        assert conn.execute("SELECT kind FROM life_events WHERE label='Bought Cart' AND month='2026-06'").fetchone()["kind"] == "purchase"
        m = conn.execute("SELECT canonical_item, flow, debt_type FROM mapping_rows WHERE item='Cart Loan'").fetchone()
        assert (m["canonical_item"], m["flow"], m["debt_type"]) == ("Cart Loan", "debt", "auto")
        a = conn.execute("SELECT kind, type, debt_item FROM accounts WHERE name='Cart Loan'").fetchone()
        assert (a["kind"], a["type"], a["debt_item"]) == ("debt", "auto", "Cart Loan")
        assert float(conn.execute("SELECT amount FROM balances WHERE account='Cart Loan' AND budget_month='2026-06'").fetchone()["amount"]) == 2500.0
    client.post("/rebuild")
    assert not any(c["kind"] == "goal" and c["ref"] == "purchase:cart" for c in _ds(client, "coming_up"))


def test_goals_json_round_trip_and_mcp(client, sample):
    from app.routes.settings import _editor_body
    with _db() as conn:
        body = json.loads(_editor_body(conn, "goals"))
    dated = [g for g in body if g["key"].startswith("purchase:")]
    assert dated and all("target_amount" in g and "target_month" in g for g in dated)
    assert all("target_amount" not in g for g in body if not g["key"].startswith("purchase:"))   # plain goals read as before
    r = client.post("/settings/goals", data={"body": json.dumps(body)})
    assert r.status_code in (200, 303)
    with _db() as conn:
        n = conn.execute("SELECT count(*) AS n FROM goals WHERE target_amount IS NOT NULL").fetchone()["n"]
    assert n == len(dated)
    from app import mcp_endpoint
    assert "planned_purchases" in mcp_endpoint.DATASETS
    acct = [r for r in _ds(client, "planned_purchases") if r["funded_kind"] == "account"]
    assert acct and all(mcp_endpoint.PUBLIC["planned_purchases"](r)["funded_by"] is None for r in acct)   # no account names leave the box
