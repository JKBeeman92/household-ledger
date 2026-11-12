"""The `recurring` dataset's 12-month window and `active` flag, which the 4-month golden fixture cannot exercise.

Builds the 18-month sample household with the standalone scripts, then drops a few late rows from ledger.csv so one
subscription lapses, and compares aggregate.py's output with an independent calculation."""
from __future__ import annotations
import csv, json, shutil, subprocess, sys
from collections import defaultdict

from conftest import ROOT, SAMPLE

PIPELINE = ROOT / "pipeline"


def run(script, wd):
    p = subprocess.run([sys.executable, "-I", str(PIPELINE / script)], cwd=wd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, f"{script} failed:\n{p.stdout}{p.stderr}"


def test_recurring_window_and_active_flag(tmp_path):
    (tmp_path / "txns").mkdir(); (tmp_path / "summaries").mkdir()
    from pipeline import filenames
    for f in SAMPLE.glob("*.csv"):   # EveryDollar-named months and the internal-format one (#161)
        if (p := filenames.parse(f.name)):
            shutil.copy(f, tmp_path / ("txns" if p[0] == "transactions" else "summaries") / f.name)
    for n in ("item_mapping.csv", "rules.json", "events.json"):
        shutil.copy(SAMPLE / n, tmp_path / n)
    shutil.copy(PIPELINE / "cpi_u.csv", tmp_path / "cpi_u.csv")
    run("build_ledger.py", tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "ledger.csv", newline="")))
    months = sorted({r["BudgetMonth"] for r in rows})
    assert len(months) == 18
    lapsed_after = months[-4]   # Internet stops here: more than two months before the last month
    still_active_until = months[-3]   # Water's last month: the boundary, still active
    keep = [r for r in rows if not (r["CanonicalItem"] == "Internet" and r["BudgetMonth"] > lapsed_after)
            and not (r["CanonicalItem"] == "Water" and r["BudgetMonth"] > still_active_until)]
    assert len(keep) < len(rows)
    with open(tmp_path / "ledger.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(keep)
    run("aggregate.py", tmp_path); run("make_datasets.py", tmp_path)

    per = defaultdict(lambda: defaultdict(float))
    for r in keep:
        if r["InTotals"] == "1" and r["Flow"] == "expense" and r["Category"] in ("Subscriptions", "Utilities", "Insurance"):
            per[r["CanonicalItem"]][r["BudgetMonth"]] += -float(r["Amount"])
    got = {d["item"]: d for d in json.load(open(tmp_path / "dash_data" / "recurring.json"))}
    assert set(got) == set(per)
    for item, v in per.items():
        assert got[item]["monthly_now"] == round(sum(v.get(m, 0) for m in months[-12:]) / 12, 2), item
        assert got[item]["active"] == (max(v) >= months[-3]), item
    assert got["Internet"]["active"] is False and got["Water"]["active"] is True and got["Electric"]["active"] is True
    assert got["Electric"]["monthly_now"] != round(sum(per["Electric"].get(m, 0) for m in months[-6:]) / 6, 2), "window not distinguishable from 6 months"
