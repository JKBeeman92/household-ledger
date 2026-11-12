"""Estimated payoff per debt (#144): the formula against known amortization cases, every edge case of the issue's table, how the
qualifiers stack, how the estimate moved since last month, and the coming_up rows."""
from __future__ import annotations
import importlib.util
import math
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("payoff", Path(__file__).resolve().parent.parent / "pipeline" / "payoff.py")
payoff = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(payoff)   # standalone module, as make_datasets.py imports it


def acct(**kw):
    return {"name": "Bag End mortgage", "kind": "debt", "type": "mortgage", "person": None, "debt_item": "Mortgage", "apr": 0.06, "escrow": 0.0, "active": True, **kw}


def bal(*pairs, source="entered"):
    return {m: {"m": m, "amount": a, "source": source} for m, a in pairs}


def est(a=None, balances=None, paid=None, closing="2026-10", loaded=None):
    return payoff.estimate(a or acct(), balances if balances is not None else bal(("2026-10", 200000)), paid if paid is not None else {("Mortgage", "2026-10"): 1199.10},
                           closing, loaded)


def codes(r):
    return [n["code"] for n in r["notes"]]


# ---- the formula ----

def test_textbook_thirty_year_loan_is_360_months():
    textbook = 200000 * 0.005 / (1 - 1.005 ** -360)
    assert round(textbook, 2) == 1199.10
    n = payoff.months_left(200000, 1199.10, 0.06)
    assert n == pytest.approx(360.00088, abs=1e-5)       # a cent's rounding leaves ~$1 for a 361st month: under the tolerance
    assert payoff.whole_months(n) == 360
    r = est()
    assert r["status"] == "date" and r["months"] == 360 and r["payoff"] == "2056-10" and not r["notes"]


@pytest.mark.parametrize("B,apr,P,months", [
    (200000, 0.06, 1199.09, 360),       # 360.009: a last sliver under 1% of a month
    (200000, 0.06, 1250.00, 323),        # 322.69       # a genuine part month rounds up
    (25000, 0.045, 466.08, 60),         # 5-year car loan
    (10000, 0.0, 250, 40),              # 0% entered: plain division, but not "at least"
    (18000, None, 275, 66),             # no APR: B/P, at least
    (100, 0.2, 5000, 1),                # paid in the first month
])
def test_known_cases(B, apr, P, months):
    assert payoff.whole_months(payoff.months_left(B, P, apr)) == months


def test_month_arithmetic_rolls_the_year():
    r = est(balances=bal(("2026-10", 600)), paid={("Mortgage", "2026-10"): 200}, a=acct(apr=None))
    assert r["months"] == 3 and r["payoff"] == "2027-01"


# ---- the edge-case table, each row ----

def test_payment_at_or_below_the_interest_never_gets_there():
    for p in (1000, 900):   # 6% of 200,000 a year is 1,000 a month
        r = est(paid={("Mortgage", "2026-10"): p})
        assert r["status"] == "not_down" and r["payoff"] is None and codes(r) == ["not_down"]
    r = est(paid={("Mortgage", "2026-10"): 1000.01})   # just over: no math error, and no absurd date
    assert r["status"] == "too_long" and r["payoff"] is None


def test_no_apr_is_at_least():
    r = est(a=acct(apr=None, type="student"), balances=bal(("2026-10", 18000)), paid={("Mortgage", "2026-10"): 275})
    assert r["months"] == 66 and r["at_least"] and codes(r) == ["no_apr"]
    r0 = est(a=acct(apr=0.0, type="student"), balances=bal(("2026-10", 18000)), paid={("Mortgage", "2026-10"): 275})
    assert r0["months"] == 66 and not r0["at_least"] and codes(r0) == []


def test_card_says_no_new_charges():
    r = est(a=acct(type="card", apr=0.24, escrow=None), balances=bal(("2026-10", 3000)), paid={("Mortgage", "2026-10"): 200})
    assert r["status"] == "date" and codes(r) == ["card"]


def test_mortgage_with_no_escrow_asks_for_it_and_escrow_comes_off_the_payment():
    assert codes(est(a=acct(escrow=None))) == ["escrow"]
    assert codes(est(a=acct(escrow=0.0))) == []                    # 0 entered is set
    r = est(a=acct(escrow=400.0), paid={("Mortgage", "2026-10"): 1599.10})
    assert r["payment"] == 1199.10 and r["months"] == 360


def test_no_payment_last_month_uses_the_three_month_average_or_says_so():
    loaded = {"2026-08", "2026-09", "2026-10"}
    paid = {("Mortgage", "2026-08"): 1800, ("Mortgage", "2026-09"): 1800}
    r = est(paid=paid, loaded=loaded)
    assert r["status"] == "date" and r["basis"] == "avg3" and r["payment"] == 1200 and codes(r) == ["avg3"]
    assert r["notes"][0]["text"].startswith("No payment in {m}") and r["notes"][0]["m"] == "2026-10"
    r = est(paid={}, loaded=loaded)
    assert r["status"] == "no_payment" and r["payoff"] is None and codes(r) == ["no_payment"]
    r = est(paid={("Mortgage", "2026-10"): 300}, a=acct(escrow=400.0), loaded=loaded)   # less than escrow: no payment to the loan
    assert r["status"] == "no_payment"


def test_estimated_balance_is_marked_but_an_accepted_suggestion_is_not():
    assert codes(est(balances=bal(("2026-10", 200000), source="estimated"))) == ["estimated"]
    assert codes(est(balances=bal(("2026-10", 200000), source="carried"))) == []


def test_qualifiers_stack_in_a_fixed_order_and_a_blocked_estimate_shows_only_its_own_sentence():
    a = acct(type="card", apr=None, escrow=None)
    r = est(a=a, balances=bal(("2026-10", 3000), source="estimated"), paid={("Mortgage", "2026-09"): 300}, loaded={"2026-09", "2026-10"})
    assert r["status"] == "date" and codes(r) == ["avg3", "no_apr", "estimated", "card"]
    r = est(a=acct(escrow=None), paid={}, loaded={"2026-10"})
    assert codes(r) == ["no_payment"]


def test_paid_off_unlinked_and_retired():
    assert est(balances=bal(("2026-10", 0)))["status"] == "paid_off"
    r = est(a=acct(debt_item=None))
    assert r["status"] == "no_link" and codes(r) == ["no_link"]
    assert est(balances=bal(("2026-09", 1000))) is None            # no balance at the closing month
    assert payoff.estimates([acct(active=False)], [{"account": "Bag End mortgage", "m": "2026-10", "amount": 1, "source": "entered"}], {}, "2026-10", set()) == []


# ---- movement since last month's estimate ----

def test_moved_since_last_months_estimate():
    # September's balance is what the October payment, less October's interest, brings to 200,000: the schedule is on course
    rows = [{"account": "Bag End mortgage", "m": m, "amount": a, "source": "entered"} for m, a in (("2026-09", 200198.11), ("2026-10", 200000))]
    same = payoff.estimates([acct()], rows, {("Mortgage", "2026-09"): 1199.10, ("Mortgage", "2026-10"): 1199.10}, "2026-10", {"2026-09", "2026-10"})[0]
    assert same["change"] == -198.11 and same["moved"] == 0 and payoff.moved_text(same) == "the same month as last month's estimate"
    extra = payoff.estimates([acct()], rows, {("Mortgage", "2026-09"): 1199.10, ("Mortgage", "2026-10"): 1600}, "2026-10", {"2026-09", "2026-10"})[0]
    assert extra["moved"] < 0 and extra["paid_more"] and payoff.moved_text(extra).endswith("sooner: you paid extra")
    less = payoff.estimates([acct()], rows, {("Mortgage", "2026-09"): 1600, ("Mortgage", "2026-10"): 1199.10}, "2026-10", {"2026-09", "2026-10"})[0]
    assert less["moved"] > 0 and payoff.moved_text(less).endswith("later")
    first = payoff.estimates([acct()], rows[1:], {("Mortgage", "2026-10"): 1199.10}, "2026-10", {"2026-10"})[0]
    assert first["moved"] is None and payoff.moved_text(first) is None


# ---- coming_up ----

def test_coming_up_rows():
    p = est()
    blocked = est(paid={("Mortgage", "2026-10"): 900})
    events = [{"m": "2026-09", "label": "past"}, {"m": "2027-03", "label": "Frodo starts school", "kind": "family"}]
    rows = payoff.coming_up([p, blocked], events, "2026-10")
    assert [r["kind"] for r in rows] == ["event", "payoff"]
    ev, po = rows
    assert ev == {"m": "2027-03", "kind": "event", "label": "Frodo starts school", "public_label": "Frodo starts school", "amount": None, "source": "planned", "ref": "Frodo starts school"}
    assert po["m"] == "2056-10" and po["label"] == "Bag End mortgage paid off (est.)" and po["public_label"] == "Mortgage paid off (est.)"
    assert po["source"] == "estimated" and po["ref"] == "Bag End mortgage" and po["amount"] is None
    assert set(po) == {"m", "kind", "label", "public_label", "amount", "source", "ref"}
    assert payoff.coming_up([], events, None) == []


# ---- through the build: the review and the datasets ----

def test_the_build_emits_payoff_and_the_review_reads_coming_up_without_account_names(client, reset_db):
    from conftest import GOLDEN, upload_files
    from app import db, review
    upload_files(client, GOLDEN / "exports")   # 2025-01 … 2025-04; the student loan has balances to 2025-03
    data = client.get("/api/datasets").json()
    (loan,) = data["payoff"]
    assert loan["m"] == "2025-04" and loan["status"] == "date" and "estimated" in [n["code"] for n in loan["notes"]]
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO life_events (month, label) VALUES ('2025-06', 'Sam starts at the Green Dragon')"); conn.commit()
    client.post("/rebuild")
    with db.pool.connection() as conn:
        ctx = review.build_context(conn, "2025-04")
    items = ctx["coming_up"]["items"]
    assert items == [{"m": "2025-06", "kind": "event", "label": "Sam starts at the Green Dragon", "amount": None, "source": "planned"}]
    assert "Lembas" not in str(ctx["coming_up"])


def test_the_check_in_shows_payoffs_under_balances_and_a_coming_up_step(client, reset_db):
    from conftest import GOLDEN, upload_files
    from app import checkin, db
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO life_events (month, label) VALUES ('2025-06', 'Sam starts at the Green Dragon')"); conn.commit()
    client.post("/rebuild")
    page = client.get("/checkin/2025-04").text
    bal = page[page.find('id="step-balances"'):page.find('id="step-goals"')]
    assert "When the debts are gone" in bal and "Lembas Student Loan" in bal and "July 2031" in bal and "From an estimated balance" in bal
    up = page[page.find('id="step-coming_up"'):]
    assert "Coming up" in up and "June 2025" in up and "Sam starts at the Green Dragon" in up and "data-view" in page
    with db.pool.connection() as conn:
        ov = checkin.overview(conn, "2025-04")
    step = next(s for s in ov["steps"] if s["key"] == "coming_up")
    assert step["status"] == "todo" and [s["key"] for s in ov["steps"]].index("coming_up") == [s["key"] for s in ov["steps"]].index("standing") + 1
    client.post("/checkin/2025-04/steps/coming_up/view")
    with db.pool.connection() as conn:
        assert next(s for s in checkin.overview(conn, "2025-04")["steps"] if s["key"] == "coming_up")["status"] == "done"
    with db.pool.connection() as conn:
        assert "coming_up" not in {s["key"] for s in checkin.overview(conn, "2025-02")["steps"]}   # not in the short form


def test_the_last_month_tab_has_the_payoff_card(client, reset_db):
    html = client.get("/").text
    assert 'data-draw="lm-payoff"' in html and 'data-source="payoff"' in html and "When the debts are gone" in html
