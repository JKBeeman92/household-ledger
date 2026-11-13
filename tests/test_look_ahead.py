"""The look ahead (#148): every figure reproducible by hand from the inputs in its row; trailing 12 and 3 months; freed payments
added to cash and listed; debts paid down by amortization; growth only when the rule sets it; debt-free excluding the mortgage;
goals reached at their pace; coming_up in range."""
from __future__ import annotations
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("look_ahead_rules", Path(__file__).resolve().parent.parent / "pipeline" / "look_ahead.py")
la = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(la)


def months(first: str, n: int) -> list[str]:
    y, m = map(int, first.split("-")); out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}"); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


MONTHLY = [{"m": m, "net": 1000.0 if i < 9 else 1600.0} for i, m in enumerate(months("2025-07", 12))]   # Jul 2025 .. Jun 2026
NW = [{"m": "2026-06", "net_worth": 50000.0}]
BAL = [{"m": "2026-06", "account": "Savings", "kind": "asset", "amount": 20000.0}, {"m": "2026-06", "account": "Car", "kind": "debt", "amount": 1000.0}]
CAR = {"m": "2026-06", "account": "Car Loan", "type": "auto", "balance": 1000.0, "payment": 250.0, "apr": None, "status": "date", "payoff": "2026-10"}
HOME = {"m": "2026-06", "account": "Home", "type": "mortgage", "balance": 200000.0, "payment": 1200.0, "apr": 0.06, "status": "date", "payoff": "2052-06"}


def row(rows, h):
    return next(r for r in rows if r["h"] == h)


def test_pace_freed_paid_down_and_net_worth_by_hand():
    rows = la.look_ahead(MONTHLY, [CAR], NW, BAL, [], [], "2026-06")
    assert [r["h"] for r in rows] == [3, 12, 36] and [r["until"] for r in rows] == ["2026-09", "2027-06", "2029-06"]
    r = row(rows, 12)
    assert r["pace"] == round((9 * 1000 + 3 * 1600) / 12, 2) == 1150.0 and r["pace_3"] == 1600.0 and r["pace_months"] == 12 and r["pace_from"] == "2025-07"
    # the car loan is paid off in October 2026 (4 months on): its $250 is freed for the other 8 of the 12 months
    assert r["freed"] == 250 * 8 == 2000.0 and r["freed_by"][0]["months"] == 8
    assert r["cash"] == 1150 * 12 + 2000 and r["cash_3"] == 1600 * 12 + 2000
    assert r["paid_down"] == 1000.0                     # no APR: four payments of 250 clear it
    assert r["growth"] == 0.0 and r["growth_pct"] is None
    assert r["net_worth"] == 50000 + 1150 * 12 + 2000 + 1000 and r["net_worth_3"] == 50000 + 1600 * 12 + 2000 + 1000
    r3 = row(rows, 3)
    assert r3["freed"] == 0.0 and r3["paid_down"] == 750.0 and r3["cash"] == 3450.0
    assert r3["debt_free"] == "2026-10" and not r3["debt_free_within"] and r["debt_free_within"]


def test_growth_only_with_the_rule_and_debt_free_leaves_the_mortgage_out():
    rows = la.look_ahead(MONTHLY, [CAR, HOME], NW, BAL, [], [], "2026-06", growth_pct=5)
    r = row(rows, 12)
    assert r["growth"] == round(20000 * 0.05, 2) and r["growth_pct"] == 5 and r["assets"] == 20000.0
    assert r["debt_free"] == "2026-10"                   # the mortgage is not counted
    # the mortgage pays down at 6%: one month is 200000 × 1.005 − 1200 = 199800
    assert la.amortize(200000, 1200, 0.06, 1) == 199800.0
    no_date = {**CAR, "status": "no_payment", "payoff": None, "payment": None}
    r = row(la.look_ahead(MONTHLY, [no_date], NW, BAL, [], [], "2026-06"), 12)
    assert r["debt_free"] is None and r["debt_free_open"] == 1 and r["freed"] == 0.0 and r["paid_down"] == 0.0
    assert row(la.look_ahead(MONTHLY, [], NW, BAL, [], [], "2026-06"), 3)["debt_free"] == "2026-06"   # no debts but a mortgage: already


def test_goals_and_coming_up_in_range_and_fewer_months():
    goals = [{"key": "a", "label": "Desk", "target_amount": 400, "target_month": "2026-09", "verdict": "reached", "reach_m": None},
             {"key": "b", "label": "Cart", "target_amount": 9000, "target_month": "2027-03", "verdict": "short", "reach_m": "2028-01"},
             {"key": "c", "label": "Boat", "target_amount": 9000, "target_month": "2027-03", "verdict": "no_pace", "reach_m": None},
             {"key": "d", "label": "Gone", "target_amount": 1, "target_month": "2026-01", "done": True}]
    cu = [{"m": "2026-08", "kind": "event", "label": "x", "public_label": "x", "amount": None}, {"m": "2027-01", "kind": "goal", "label": "y", "public_label": "y", "amount": 1}]
    rows = la.look_ahead(MONTHLY[-2:], [], NW, BAL, goals, cu, "2026-06")
    r3, r36 = row(rows, 3), row(rows, 36)
    assert r3["pace_months"] == 2 and r3["pace"] == 1600.0
    assert {g["key"]: g["within"] for g in r3["goals"]} == {"a": True, "b": False, "c": False}
    assert {g["key"]: g["within"] for g in r36["goals"]} == {"a": True, "b": True, "c": False}
    assert [c["m"] for c in r3["coming_up"]] == ["2026-08"] and len(row(rows, 12)["coming_up"]) == 2
    assert la.look_ahead([], [], [], [], [], [], None) == []
    assert row(la.look_ahead(MONTHLY, [], [], BAL, [], [], "2026-06"), 3)["net_worth"] is None   # no net worth yet: no projection of it


# ---- through the app ----

import pytest   # noqa: E402

from conftest import SAMPLE   # noqa: E402


@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def test_sample_household_look_ahead_reaches_the_dashboard_checkin_review_and_mcp(client, sample):
    rows = client.get("/api/datasets").json()["look_ahead"]
    assert [r["h"] for r in rows] == [3, 12, 36] and all(r["m"] == "2026-06" for r in rows)
    r = rows[1]
    assert r["pace_months"] == 12 and r["net_worth_now"] is not None
    assert r["cash"] == round(r["pace"] * 12 + r["freed"], 2)                                        # reproducible from the row
    assert r["net_worth"] == round(r["net_worth_now"] + r["cash"] + r["paid_down"] + r["growth"], 2)
    assert {g["label"] for g in r["goals"]} == {"A trip to Rivendell", "A new cart"}
    page = client.get("/").text
    assert page.count('data-draw="ahead-rows"') == 2 and 'id="lm-ahead"' in page and 'id="q-ahead"' in page
    assert "Where the current pace leads" in client.get("/checkin/2026-06").text   # June ends a quarter: the quarter step reads it
    from app import db, review
    with db.pool.connection() as conn:
        ctx = review.build_context(conn, "2026-06")
    assert [h["h"] for h in ctx["look_ahead"]["horizons"]] == [3, 12, 36] and "account" not in str(ctx["look_ahead"])
    from app import mcp_endpoint
    assert "look_ahead" in mcp_endpoint.DATASETS
    assert all("account" not in f for x in rows for f in mcp_endpoint.PUBLIC["look_ahead"](x)["freed_by"])


def test_growth_rule_is_used_and_labelled(client, sample):
    from app import db
    with db.pool.connection() as conn:
        rules = db.get_setting(conn, "rules", {}) or {}
        db.put_setting(conn, "rules", {**rules, "assumed_growth_pct": 4}); conn.commit()
    client.post("/rebuild")
    r = client.get("/api/datasets").json()["look_ahead"][1]
    assert r["growth_pct"] == 4 and r["growth"] == round(r["assets"] * 0.04, 2)
    assert "with 4% a year growth on assets" in client.get("/checkin/2026-06").text
