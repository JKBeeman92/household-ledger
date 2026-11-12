"""Net worth (#146): assets − debts each month; the change splits into what you did and what happened to you, which add up (with
accounts added or retired, kept apart) to the total change; a new account is labelled, not counted as growth."""
from __future__ import annotations
import json

import pytest

from conftest import GOLDEN, SAMPLE, upload_files
from app import db, review, mcp_endpoint
from pipeline import balances as bal, import_exports


def acct(name, kind="asset", typ="savings", nw=True):
    return {"name": name, "kind": kind, "type": typ, "person": None, "debt_item": None, "apr": None, "escrow": None,
            "emergency": False, "in_net_worth": nw, "active": True}


def adds_up(r):
    return r["change"] == pytest.approx((r["you_did"] or 0) + (r["happened"] or 0) + (r["added"] or 0) - (r["closed"] or 0), abs=0.005)


def test_split_and_a_new_account():
    accts = [acct("Savings"), acct("Loan", "debt", "student"), acct("Smial", typ="home"), acct("Jar", nw=False)]
    stored = {("Savings", "2025-01"): (1000, "entered"), ("Savings", "2025-02"): (1600, "entered"), ("Loan", "2025-01"): (5000, "entered"),
              ("Loan", "2025-02"): (4800, "entered"), ("Smial", "2025-02"): (200000, "entered"), ("Jar", "2025-01"): (50, "entered")}
    rows = bal.series(accts, stored, {}, "2025-02")
    nw = {r["m"]: r for r in bal.net_worth(rows, {"2025-02": 700.0})}
    jan, feb = nw["2025-01"], nw["2025-02"]
    assert jan["net_worth"] == 1000 - 5000 and jan["change"] is None   # Jar is out of net worth
    assert feb["net_worth"] == 1600 - 4800 + 200000 and feb["change"] == 200800
    assert feb["added"] == 200000 and feb["added_accounts"] == 1            # the home's first month: labelled, not growth
    assert feb["you_did"] == 700 and feb["happened"] == 800 - 700 and adds_up(feb)   # +600 saved, −200 owed: 800 moved, 700 of it yours
    assert feb["net_worth_ex_home"] == 1600 - 4800 and feb["home_change"] == 0


def test_twelve_month_change_needs_a_year_of_balances():
    accts = [acct("Savings")]
    months = bal.months("2024-01", "2025-03")
    stored = {("Savings", m): (1000 + 100 * i, "entered") for i, m in enumerate(months)}
    nw = {r["m"]: r for r in bal.net_worth(bal.series(accts, stored, {}, "2025-03"), {m: 60.0 for m in months})}
    assert nw["2024-12"]["change_12"] is None   # 2023-12 has no balance
    assert nw["2025-01"]["change_12"] == 1200 and nw["2025-03"]["change_12"] == 1200 and nw["2025-03"]["you_did_12"] == 720 and nw["2025-03"]["happened_12"] == 480


def test_golden_split_adds_up_and_reaches_review_and_mcp(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    rows = client.get("/api/datasets").json()["net_worth"]
    assert all(adds_up(r) for r in rows if r["change"] is not None)
    mar = next(r for r in rows if r["m"] == "2025-03")
    assert mar["closed"] == 1250 and mar["accounts"] == 3   # Bywater Checking, retired after February, is not a loss
    with db.pool.connection() as conn:
        ctx = review.build_context(conn, "2025-03")
    assert ctx["net_worth"]["net_worth"] == mar["net_worth"] and "Green Hills" not in json.dumps(ctx["net_worth"])
    assert "net_worth" in mcp_endpoint.DATASETS


def test_sample_household_split_adds_up_every_month(client, reset_db):
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    rows = client.get("/api/datasets").json()["net_worth"]
    assert len(rows) == 24 and all(adds_up(r) for r in rows if r["change"] is not None and r["you_did"] is not None)
    assert [r["m"] for r in rows if r["you_did"] is None] == ["2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12"]   # before the exports
    assert sum(r["added_accounts"] for r in rows) == 1   # the cart, first valued in 2024-09
    assert rows[-1]["change_12"] is not None and rows[-1]["you_did_12"] is not None
