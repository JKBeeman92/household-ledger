"""Money held for someone else (#107): a month where one expense or debt item takes in many times its usual amount and pays at
least half of it back out in the months after is a finding, beside UNMAPPED and reconciliation. Runs build_ledger.py standalone on
the sample household (whose Venmo Pass-Through is mapped exclude, so the test maps it expense, as a household that has not set
exclude yet would have it), then end to end through the build log and Close a month."""
from __future__ import annotations
import csv, json, os, shutil, subprocess, sys
from pathlib import Path

import psycopg
import pytest

from conftest import GOLDEN, ROOT, SAMPLE
from pipeline import import_exports

HELD = [("2025-09", "Venmo Pass-Through"), ("2026-03", "Venmo Pass-Through")]   # generate.py's PASS_THROUGH: collected, then paid out


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
    p = subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / "build_ledger.py")], cwd=wd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads((wd / "pass_through.json").read_text())


def hits(found: list[dict]) -> list[tuple[str, str]]:
    return [(h["m"], h["item"]) for h in found]


def test_sample_households_pass_through_months_are_flagged(tmp_path):
    found = build(tmp_path)
    assert hits(found) == HELD
    sep = found[0]
    assert sep["items"] == ["Venmo Pass-Through"] and sep["inflow"] == 1140 and sep["median"] == 0 and sep["window"] == 3
    assert sep["later_outflows"] >= 1140   # the present, plus the dinner splits of the months after


def test_an_item_already_excluded_is_not_flagged_and_nothing_else_in_the_sample_is(tmp_path):
    assert build(tmp_path, venmo="exclude") == []


REFUND = ["Household", "Household Supplies", "Income", "06/16/2025", "Michel Delving Emporium", "Bywater Checking", "1200.00", "refund"]
JUNE, JULY = "5ca1ab1e-06-2025-EveryDollar-Transactions.csv", "5ca1ab1e-07-2025-EveryDollar-Transactions.csv"


def test_a_refund_that_is_not_paid_back_out_is_not_flagged(tmp_path):
    """Conditions 1 and 2 hold for the refund (well over five usual months, over the floor); 3 does not, and that is the point."""
    assert hits(build(tmp_path / "refund", venmo="exclude", extra={JUNE: [REFUND]})) == []
    paid_out = ["Household", "Household Supplies", "Expense", "07/02/2025", "Michel Delving Emporium", "Bywater Checking", "-700.00", ""]
    assert hits(build(tmp_path / "paid", venmo="exclude", extra={JUNE: [REFUND], JULY: [paid_out]})) == [("2025-06", "Household Supplies")]


@pytest.mark.parametrize("key", ["pass_through_multiple", "pass_through_floor", "pass_through_window"])
def test_emptying_any_of_the_three_rules_turns_the_finding_off(tmp_path, key):
    assert build(tmp_path, {key: None}) == []


def test_the_rules_are_the_thresholds(tmp_path):
    assert hits(build(tmp_path / "floor", {"pass_through_floor": 1000})) == HELD[:1]     # 640 is under it, 1140 is not
    assert hits(build(tmp_path / "window", {"pass_through_window": 1})) == HELD          # both are half paid out within a month
    assert build(tmp_path / "pad", {"pass_through_floor": 0, "pass_through_multiple": 1, "pass_through_window": 12}) != []


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
    assert "\nPass-through: 2025-09 Venmo Pass-Through: 1,140.00 came in" in detail and "Set Flow exclude on Venmo Pass-Through." in detail
    page = client.get("/upload").text
    assert 'id="pass-through-panel"' in page and page.count("Set Flow <code>exclude</code> on <code>Venmo Pass-Through</code>.") == 2
