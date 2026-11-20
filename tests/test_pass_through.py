"""Money held for someone else (#107): a month where an expense or debt item that is usually quiet takes in a sum that is large
for the household (over a share of its usual monthly income) is a finding, beside UNMAPPED and reconciliation; what went back out
through the item after is shown as evidence, never a condition. Runs build_ledger.py standalone on the sample household (whose
Venmo Pass-Through is mapped exclude, so the test maps it expense, as a household that has not set exclude yet would have it) and on
a synthetic household that reproduces what a real back catalogue showed, then end to end through the build log and Close a month."""
from __future__ import annotations
import csv, json, os, shutil, subprocess, sys
from pathlib import Path

import psycopg
import pytest

from conftest import GOLDEN, ROOT, SAMPLE
from pipeline import import_exports
from pipeline.run_pipeline import describe_pass_through

HELD = [("2025-09", "Venmo Pass-Through"), ("2026-03", "Venmo Pass-Through")]   # generate.py's PASS_THROUGH: collected, then paid out


def run_build(wd: Path) -> list[dict]:
    p = subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / "build_ledger.py")], cwd=wd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads((wd / "pass_through.json").read_text())


def build(wd: Path, rules: dict | None = None, venmo: str = "expense", extra: dict[str, list[list[str]]] | None = None) -> list[dict]:
    """build_ledger.py on the sample household in a folder of CSVs; extra: export file name -> rows appended to it."""
    (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir()
    for f in SAMPLE.glob("*.csv"):
        if "ransactions" in f.name: shutil.copy(f, wd / "txns" / f.name)
        elif "udget" in f.name: shutil.copy(f, wd / "summaries" / f.name)
    shutil.copy(GOLDEN / "cpi_u.csv", wd / "cpi_u.csv")
    (wd / "rules.json").write_text(json.dumps({**json.loads((SAMPLE / "rules.json").read_text()), **(rules or {})}))
    mapping = list(csv.DictReader(open(SAMPLE / "item_mapping.csv", newline="")))
    with open(wd / "item_mapping.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(mapping[0])); w.writeheader()
        w.writerows([dict(r, Flow=venmo) if r["Item"] == "Venmo Pass-Through" else r for r in mapping])
    for name, rows in (extra or {}).items():
        with open(wd / "txns" / name, "a", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows(rows)
    return run_build(wd)


def hits(found: list[dict]) -> list[tuple[str, str]]:
    return [(h["m"], h["item"]) for h in found]


def test_sample_households_pass_through_months_are_flagged(tmp_path):
    found = build(tmp_path)
    assert hits(found) == HELD
    sep, mar = found
    assert sep["items"] == ["Venmo Pass-Through"] and sep["inflow"] == 1140 and sep["median"] == 0 and sep["window"] == 3
    # the floor is a tenth of the household's usual monthly income over the 12 months before (pass_through_income_share 0.10)
    assert sep["floor"] == round(sep["income"] * 0.10, 2) and 500 < sep["floor"] < mar["inflow"] < sep["inflow"]
    # evidence, not a condition: the present, plus the dinner splits of the months after
    assert sep["later_outflows"] >= 1140 and sep["months_after"] == 3


def test_an_item_already_excluded_is_not_flagged_and_nothing_else_in_the_sample_is(tmp_path):
    assert build(tmp_path, venmo="exclude") == []


JUNE, JULY, AUGUST = ("5ca1ab1e-06-2025-EveryDollar-Transactions.csv", "5ca1ab1e-07-2025-EveryDollar-Transactions.csv",
                      "5ca1ab1e-08-2025-EveryDollar-Transactions.csv")


def eating_out(day: str, amount: str) -> list[str]:
    return ["Food", "Eating Out", "Income" if amount[0] != "-" else "Expense", day, "Michel Delving Kitchen", "Bywater Checking", amount, ""]


def test_a_refund_on_an_item_spent_every_month_is_not_flagged(tmp_path):
    """Eating Out is spent every month (median about 140): a refund there nets about 500, over the dollar floor but under five usual
    months, so its usual month is what keeps it out, whatever the income floor; at twice usual it would be listed."""
    extra = {JUNE: [eating_out("06/16/2025", "650.00")]}
    assert hits(build(tmp_path / "default", venmo="exclude", extra=extra)) == []
    assert hits(build(tmp_path / "no-income-floor", {"pass_through_income_share": 0}, venmo="exclude", extra=extra)) == []
    assert hits(build(tmp_path / "m2", {"pass_through_income_share": 0, "pass_through_multiple": 2}, venmo="exclude", extra=extra)) == [("2025-06", "Eating Out")]


def test_a_large_refund_on_an_item_spent_every_month_is_flagged(tmp_path):
    """The known trade-off (README), kept as the earlier test's case: a 1,200 refund on Eating Out (usual month about 140) or on
    Household Supplies (about 93) is more than five usual months and over the household's floor, so it is listed; leave it."""
    supplies = ["Household", "Household Supplies", "Income", "06/16/2025", "Bywater Hardware", "Bywater Checking", "1200.00", ""]
    found = build(tmp_path / "eat", venmo="exclude", extra={JUNE: [eating_out("06/16/2025", "1200.00")]})
    assert hits(found) == [("2025-06", "Eating Out")] and 5 * found[0]["median"] < found[0]["inflow"] < 10 * found[0]["median"]
    assert hits(build(tmp_path / "eat10", {"pass_through_multiple": 10}, venmo="exclude",
                      extra={JUNE: [eating_out("06/16/2025", "1200.00")]})) == []   # a household that minds raises the multiple
    assert hits(build(tmp_path / "sup", venmo="exclude", extra={JUNE: [supplies]})) == [("2025-06", "Household Supplies")]


REFUND = ["Vehicle", "Cart Registration", "Income", "06/16/2025", "Hobbiton Cart Office", "Bywater Checking", "1200.00", "refund"]


def test_a_large_refund_on_an_item_that_is_usually_empty_is_flagged(tmp_path):
    """The known trade-off (README): Cart Registration is paid once a year, so its usual month is 0, and a refund over the household's
    floor looks like money held for someone else. Nothing went back out through it, and the finding says so."""
    found = build(tmp_path, venmo="exclude", extra={JUNE: [REFUND]})
    assert hits(found) == [("2025-06", "Cart Registration")] and found[0]["median"] == 0 and found[0]["later_outflows"] == 0
    assert "none went back out through it in the 3 months after (a transfer may have carried it)" in describe_pass_through(found)[0]


def test_the_window_rule_moves_the_evidence_not_the_finding(tmp_path):
    """Paid out in August, two months after the June inflow: inside a window of 3, outside a window of 1. Listed either way."""
    paid_late = ["Vehicle", "Cart Registration", "Expense", "08/05/2025", "Hobbiton Cart Office", "Bywater Checking", "-1100.00", ""]
    extra = {JUNE: [REFUND], AUGUST: [paid_late]}
    w3 = build(tmp_path / "w3", venmo="exclude", extra=extra)
    w1 = build(tmp_path / "w1", {"pass_through_window": 1}, venmo="exclude", extra=extra)
    off = build(tmp_path / "off", {"pass_through_window": None}, venmo="exclude", extra=extra)
    assert hits(w3) == hits(w1) == hits(off) == [("2025-06", "Cart Registration")]
    assert w3[0]["later_outflows"] == 1100 and w1[0]["later_outflows"] == 0 and off[0]["later_outflows"] is None
    assert "1,100.00 more than usual went back out through it in the 3 months after" in describe_pass_through(w3)[0]
    assert "went back out" not in describe_pass_through(off)[0]


@pytest.mark.parametrize("key", ["pass_through_multiple", "pass_through_income_share", "pass_through_floor"])
def test_emptying_a_threshold_rule_turns_the_finding_off(tmp_path, key):
    assert build(tmp_path, {key: None}) == []


def test_the_rules_are_the_thresholds(tmp_path):
    assert hits(build(tmp_path / "floor", {"pass_through_floor": 1000})) == HELD[:1]          # 640 is under it, 1140 is not
    assert hits(build(tmp_path / "share", {"pass_through_income_share": 0.15})) == HELD[:1]   # about 880: the same
    assert hits(build(tmp_path / "window", {"pass_through_window": 1})) == HELD               # the window is evidence only
    assert build(tmp_path / "pad", {"pass_through_floor": 0, "pass_through_income_share": 0, "pass_through_multiple": 1}) != HELD


# ---- a household that reproduces what a real back catalogue showed, without its numbers ----
# Usual monthly income 9,000. Five quiet items (savings and giving envelopes, usually 0 to 90 a month) each take in a few hundred
# dollars once (a fund topped back up, a tax refund), then spend far more the month after: the earlier, paid-back-out test listed
# all five. One envelope that is always empty collects 20,000 for a relative; 4,000 of it leaves through the envelope over the next
# months and the rest is handed over by a transfer, which the ledger leaves out: the earlier test missed it.
MONTHS = [f"{2024 + i // 12}-{i % 12 + 1:02d}" for i in range(20)]   # 2024-01 .. 2025-08
QUIET = {   # item: (usual monthly outflow, month of the inflow, inflow, spent the month after)
    "Emergency Fund": (-50.00, "2024-11", 600.00, -2400.00),
    "Vacation Fund": (0.00, "2025-02", 520.00, -1900.00),
    "Tax Payments": (-80.00, "2025-03", 450.00, -1500.00),
    "Education Fund": (-25.00, "2025-04", 640.00, -1200.00),
    "Tithe": (-90.00, "2025-05", 560.00, -800.00),
}
DONATION = ("Family Help", "2025-01")


def quiet_household(wd: Path, rules: dict | None = None) -> Path:
    (wd / "txns").mkdir(parents=True)
    shutil.copy(GOLDEN / "cpi_u.csv", wd / "cpi_u.csv")
    (wd / "rules.json").write_text(json.dumps(rules or {}))
    with open(wd / "item_mapping.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note"])
        w.writerows([["Paycheck", "Paycheck", "Income", "income", ""], ["Groceries", "Groceries", "Food", "expense", ""],
                     [DONATION[0], DONATION[0], "Giving", "expense", ""]] + [[i, i, "Savings", "expense", ""] for i in QUIET])
    for k, m in enumerate(MONTHS):
        nxt = MONTHS[k - 1] if k else ""
        rows = [["Income", "Paycheck", "Income", f"{m}-01", "Employer Payroll", "Checking", "4500.00", ""],
                ["Income", "Paycheck", "Income", f"{m}-15", "Employer Payroll", "Checking", "4500.00", ""],
                ["Food", "Groceries", "Expense", f"{m}-10", "Market", "Checking", f"{-900 - 7 * (k % 5):.2f}", ""]]
        for item, (usual, at, inflow, after) in QUIET.items():
            if usual and m != at: rows.append(["Savings", item, "Expense", f"{m}-05", "Bank", "Checking", f"{usual:.2f}", ""])
            if m == at: rows.append(["Savings", item, "Income", f"{m}-20", "Bank", "Checking", f"{inflow:.2f}", "back into the fund"])
            if nxt == at: rows.append(["Savings", item, "Expense", f"{m}-12", "Outfitter", "Checking", f"{after:.2f}", ""])
        if m == DONATION[1]:
            rows += [["Giving", DONATION[0], "Income", f"{m}-{d:02d}", "Friends", "Checking", "2500.00", "for Mum and Dad"] for d in range(3, 27, 3)]
            rows.append(["Giving", DONATION[0], "Expense", f"{m}-28", "Transfer", "Checking", "-16000.00", "handed over"])
        if MONTHS.index(m) - MONTHS.index(DONATION[1]) in (1, 2):
            rows.append(["Giving", DONATION[0], "Expense", f"{m}-09", "Pharmacy", "Checking", "-2000.00", "for Mum and Dad"])
        with open(wd / "txns" / f"{m}-transactions.csv", "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["Group", "Item", "Type", "Date", "Merchant", "Account", "Amount", "Note"])
            w.writerows(rows)
    return wd


def test_sinking_fund_inflows_on_quiet_items_are_not_flagged_and_a_donation_left_by_transfer_is(tmp_path):
    found = run_build(quiet_household(tmp_path))
    assert hits(found) == [DONATION[::-1]]
    d = found[0]
    assert d["inflow"] == 20000 and d["median"] == 0 and d["income"] == 9000 and d["floor"] == 900
    # most of it left by the transfer, which the ledger leaves out: only what left through the envelope is evidence
    assert d["later_outflows"] == 4000 and d["months_after"] == 3
    assert "4,000.00 more than usual went back out through it in the 3 months after" in describe_pass_through(found)[0]


def test_the_quiet_items_are_kept_out_by_the_income_floor_alone(tmp_path):
    """Every one of the five passes the usual-month test and the dollar floor: only the income floor keeps them out."""
    found = run_build(quiet_household(tmp_path, {"pass_through_income_share": 0}))
    assert sorted(hits(found)) == sorted([DONATION[::-1]] + [(at, item) for item, (_, at, _, _) in QUIET.items()])


def test_a_loan_from_family_is_not_flagged(tmp_path):
    """A debt item in a loan_categories category (Family Loan by default) takes in the loan and repays it by design; the Method tab
    counts it, so it is never money held for someone else. With the category taken out of the rule it would be listed."""
    wd = quiet_household(tmp_path / "loan")
    with open(wd / "item_mapping.csv", "a", newline="") as f:
        csv.writer(f, lineterminator="\n").writerow(["Loan From Parents", "Loan From Parents", "Family Loan", "debt", ""])
    for k, m in enumerate(MONTHS[12:16]):
        row = ["Debt", "Loan From Parents", "Income", f"{m}-07", "Parents", "Checking", "15000.00", ""] if not k else \
              ["Debt", "Loan From Parents", "Expense", f"{m}-07", "Parents", "Checking", "-500.00", ""]
        with open(wd / "txns" / f"{m}-transactions.csv", "a", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(row)
    assert hits(run_build(wd)) == [DONATION[::-1]]
    (wd / "rules.json").write_text(json.dumps({"loan_categories": ["None Here"]}))
    assert sorted(hits(run_build(wd))) == sorted([DONATION[::-1], (MONTHS[12], "Loan From Parents")])


def test_the_first_export_month_is_not_judged(tmp_path):
    """With no month before it there is no usual month or usual income to compare with, so even a large inflow is left alone."""
    wd = quiet_household(tmp_path)
    with open(wd / "txns" / f"{MONTHS[0]}-transactions.csv", "a", newline="") as f:
        csv.writer(f, lineterminator="\n").writerow(["Giving", DONATION[0], "Income", f"{MONTHS[0]}-20", "Friends", "Checking", "5000.00", ""])
    assert hits(run_build(wd)) == [DONATION[::-1]]


def test_the_finding_reaches_the_build_log_and_close_a_month(client, reset_db):
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    assert 'id="pass-through-panel"' not in client.get("/upload").text   # as shipped the item is excluded: nothing to say
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE item_mapping SET flow='expense' WHERE item='Venmo Pass-Through'"); conn.commit()
    client.post("/rebuild")
    assert client.get("/api/build").json()["state"] == "ok"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        findings, detail = conn.execute("SELECT findings, detail FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    assert hits(findings["pass_through"]) == HELD and findings["reconciliation"]["clean"] and findings["unmapped"] == []
    assert "\nPass-through: 2025-09 Venmo Pass-Through: 1,140.00 came in, against a usual 0.00 a month and a floor of " in detail
    assert "more than usual went back out through it in the 3 months after. Money held" in detail and "Set Flow exclude on Venmo Pass-Through." in detail
    page = client.get("/upload").text
    assert 'id="pass-through-panel"' in page and 'id="pass-through-intro">An item that is usually quiet took in' in page   # one item, two months
    assert "more than usual, in the 3 months after" in page and "for this household</div>" in page   # the evidence and the floor
    # each flagged item links to its own mapping row, and the table scrolls by keyboard as well (DS-A11Y-1)
    assert page.count('<a href="/settings?find=Venmo%20Pass-Through#mapping">Venmo Pass-Through</a>') == 2
    assert 'aria-label="Months with money held for someone else"' in page
    # the two sentences of each log line are apart
    assert "after. Money held for someone else? Set Flow exclude on Venmo Pass-Through." in detail
    # nothing went back out through the item (handed over by a transfer), or the window rule emptied: the page says which
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        held = [dict(findings["pass_through"][0], later_outflows=0), dict(findings["pass_through"][1], later_outflows=None)]
        conn.execute("UPDATE build_log SET findings = findings || jsonb_build_object('pass_through', %s::jsonb) "
                     "WHERE id = (SELECT max(id) FROM build_log)", (json.dumps(held),)); conn.commit()
    page = client.get("/upload").text
    assert "None through this item in the 3 months after<div class=\"muted\">A transfer may have carried it.</div>" in page
    assert '<span class="muted">Not shown</span>' in page
