"""The household-specific behaviour that used to be hard-coded is now a rule (issue #91): with no rules the pipeline applies
only generic defaults, and each rule changes what it says it changes. Runs the standalone scripts on the golden exports."""
from __future__ import annotations
import csv, json, shutil
from pathlib import Path

import pytest

from conftest import GOLDEN
from test_pipeline_golden import run_scripts

from app import schema


def build(tmp_path: Path, rules: dict, extra_txn: list[list[str]] | None = None) -> Path:
    (tmp_path / "txns").mkdir(parents=True); (tmp_path / "summaries").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        shutil.copy(f, tmp_path / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    for n in ("item_mapping.csv", "cpi_u.csv", "events.json"):
        shutil.copy(GOLDEN / n, tmp_path / n)
    (tmp_path / "rules.json").write_text(json.dumps(rules))
    if extra_txn:
        p = tmp_path / "txns" / "c0ffee-01-2025-EveryDollar-Transactions.csv"
        with open(p, "a", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows(extra_txn)
    run_scripts(tmp_path)
    return tmp_path


def ds(wd: Path, name: str):
    return json.loads((wd / "dash_data" / f"{name}.json").read_text())


def ledger(wd: Path):
    return list(csv.DictReader(open(wd / "ledger.csv", newline="")))


def test_no_rules_means_no_catchalls_and_generic_goal_config(tmp_path):
    wd = build(tmp_path, {})
    assert ds(wd, "slush") == []
    assert ds(wd, "goal_config") == [{"giving_categories": ["Giving"], "housing_categories": ["Housing", "Utilities"],
                                      "savings_groups": [], "catchall_items": [], "paycheck_min": 500,
                                      "plan_keep_categories": ["Housing", "Debt", "Insurance"]}]


def test_catchall_items_and_goal_groups_come_from_the_rules(tmp_path):
    wd = build(tmp_path, {"catchall_items": ["Groceries"], "goal_savings_groups": ["Savings"], "goal_housing_categories": ["Housing"]})
    assert {r["item"] for r in ds(wd, "slush")} == {"Groceries"}
    cfg = ds(wd, "goal_config")[0]
    assert cfg["savings_groups"] == ["Savings"] and cfg["housing_categories"] == ["Housing"] and cfg["giving_categories"] == ["Giving"]


def test_paycheck_min_decides_what_counts_as_a_deposit(tmp_path):
    low = build(tmp_path / "low", {"paycheck_min": 0})
    high = build(tmp_path / "high", {"paycheck_min": 5000})
    assert sum(r["n"] for r in ds(low, "employer")) > 0
    assert sum(r["n"] for r in ds(high, "employer")) == 0


ROW = ["Food", "Groceries", "Expense", "01/20/2025", "Second Breakfast Fund", "Bywater Checking", "-0.00", ""]


def test_household_transfer_names_are_not_built_in_but_can_be_added(tmp_path):
    plain = [r for r in ledger(build(tmp_path / "a", {}, [ROW])) if r["Merchant"] == "Second Breakfast Fund"]
    assert plain and plain[0]["IsTransfer"] == "0"
    ruled = [r for r in ledger(build(tmp_path / "b", {"transfer_merchants": "second breakfast fund"}, [ROW])) if r["Merchant"] == "Second Breakfast Fund"]
    assert ruled[0]["IsTransfer"] == "1"


@pytest.mark.parametrize("merchant, kept", [("Bywater Property Co", "Bywater Property Co"), ("Green Grocer Phoenix AZ", "Green Grocer Phoenix"),
                                            ("Ferry Inn Buckland TX", "Ferry Inn Buckland"), ("Golden Perch Or", "Golden Perch Or")])
def test_trailing_state_codes_are_stripped_but_not_business_words(tmp_path, monkeypatch, merchant, kept):
    import importlib.util
    monkeypatch.chdir(build(tmp_path, {}))
    spec = importlib.util.spec_from_file_location("bl", Path(__file__).resolve().parent.parent / "pipeline" / "build_ledger.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.merchant_norm(merchant) == kept


def test_new_rule_keys_validate_and_negative_threshold_is_refused():
    s = schema.load("rules")
    ok = {"catchall_items": ["Taxes"], "paycheck_min": 250, "goal_giving_categories": None, "goal_housing_categories": ["Housing"],
          "goal_savings_groups": ["Long-expected Party"], "transfer_merchants": "party tree rebalance", "verify_transfer_prefixes": ["Slush"]}
    assert schema.validate(ok, s) == []
    assert any("at least 0" in e for e in schema.validate({"paycheck_min": -1}, s))
    assert any("regular expression" in e for e in schema.validate({"transfer_merchants": "(unclosed"}, s))
