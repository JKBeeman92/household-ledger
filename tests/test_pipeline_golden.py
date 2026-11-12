"""The four pipeline scripts, run as standalone scripts on a folder of CSVs, must reproduce the checked-in goldens exactly.

To change the goldens deliberately (a pipeline change you meant to make), run once with UPDATE_GOLDENS=1, read the git diff,
and commit it. They are never rewritten otherwise."""
from __future__ import annotations
import csv, json, os, shutil, subprocess, sys
from pathlib import Path

import pytest

from conftest import GOLDEN, ROOT

PIPELINE = ROOT / "pipeline"
SCRIPTS = ["build_ledger.py", "aggregate.py", "make_datasets.py", "make_budget_items.py"]
REFS = ["item_mapping.csv", "cpi_u.csv", "rules.json", "events.json", "accounts.csv", "balances.csv"]
EXPECTED = GOLDEN / "expected"


def run_scripts(wd: Path) -> None:
    for s in SCRIPTS:
        p = subprocess.run([sys.executable, "-I", str(PIPELINE / s)], cwd=wd, capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, f"{s} failed:\n{p.stdout}{p.stderr}"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    wd = tmp_path_factory.mktemp("pipeline")
    (wd / "txns").mkdir(); (wd / "summaries").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        shutil.copy(f, wd / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    for n in REFS:
        shutil.copy(GOLDEN / n, wd / n)
    run_scripts(wd)
    if os.environ.get("UPDATE_GOLDENS") == "1":
        shutil.rmtree(EXPECTED, ignore_errors=True)
        (EXPECTED / "dash_data").mkdir(parents=True)
        shutil.copy(wd / "ledger.csv", EXPECTED / "ledger.csv")
        for p in (wd / "dash_data").glob("*.json"):
            shutil.copy(p, EXPECTED / "dash_data" / p.name)
        pytest.skip("UPDATE_GOLDENS=1: goldens rewritten, review `git diff tests/fixtures` and run again without it")
    return wd


def test_ledger_matches_golden(built):
    assert (built / "ledger.csv").read_text() == (EXPECTED / "ledger.csv").read_text()


def test_datasets_match_golden(built):
    got = {p.name: p for p in (built / "dash_data").glob("*.json")}
    want = {p.name: p for p in (EXPECTED / "dash_data").glob("*.json")}
    assert sorted(got) == sorted(want)
    for name, p in want.items():
        assert json.loads(got[name].read_text()) == json.loads(p.read_text()), name
        assert got[name].read_text() == p.read_text(), f"{name}: same data, different bytes"


def test_fixture_still_exercises_the_special_cases(built):
    """Guards the fixture itself: the goldens are only worth something while they cover these."""
    rows = list(csv.DictReader(open(built / "ledger.csv", newline="")))
    assert len({r["BudgetMonth"] for r in rows}) == 4
    assert not any(r["Item"] == "Venmo Pass-Through" for r in rows), "excluded item leaked into the ledger"
    assert json.load(open(built / "excluded.json")) == {"Venmo Pass-Through": 2}
    pairs = [r for r in rows if r["TransferPair"]]
    assert len(pairs) == 2 and pairs[0]["TransferPair"] == pairs[1]["TransferPair"] and all(r["InTotals"] == "0" for r in pairs)
    assert [r["Item"] for r in rows if r["IsPlug"] == "1"] == ["Balancing Plug"]
    income_transfer = [r for r in rows if r["IsTransfer"] == "1" and r["Group"] == "Income"]
    assert len(income_transfer) == 1 and income_transfer[0]["InTotals"] == "0" and not income_transfer[0]["TransferPair"]
    assert any(r["BudgetMonth"] != r["CalendarMonth"] for r in rows), "no row where budget month differs from calendar month"
