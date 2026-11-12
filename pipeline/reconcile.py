#!/usr/bin/env python3 -I
"""Reconcile raw exports: sum of transactions per (budget month, item) vs the BudgetItems "Spent" column.

Standalone, like the other pipeline scripts: run it in a folder holding txns/ and summaries/ (the staged monthly exports, internal format or EveryDollar's).
Writes reconciliation_new_rows.csv and new_rows_with_budget_month.csv there and exits 1 if anything does not reconcile."""
import csv, glob, os, re, sys
from collections import defaultdict, Counter
from decimal import Decimal as D
import sys as _sys; _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # filenames.py sits beside this script
import filenames   # noqa: E402

def norm(x): return re.sub(r"\s+", " ", (x or "").replace("\u2006", " ").replace("\xa0", " ")).strip()

ROOT = os.getcwd()

if not (os.path.isdir(f"{ROOT}/txns") and os.path.isdir(f"{ROOT}/summaries")):
    sys.exit("reconcile.py: run it in a folder containing txns/ and summaries/ (monthly CSV exports)")

# --- transactions with Budget Month from filename ---
txn = []
for bm, f in ((bm, f"{ROOT}/txns/{fn}") for bm, fn in filenames.exports(f"{ROOT}/txns", "transactions")):
    with open(f, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            r["Item"] = norm(r["Item"]); r["BudgetMonth"] = bm; r["SourceFile"] = os.path.basename(f)
            txn.append(r)

# --- summaries ---
summ = {}  # (bm, item) -> (group, planned, spent)
for bm, f in ((bm, f"{ROOT}/summaries/{fn}") for bm, fn in filenames.exports(f"{ROOT}/summaries", "budget_items")):
    with open(f, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            summ[(bm, norm(r["Item"]))] = (r["Group"], D(r["Planned"]), D(r["Spent"]))

# --- aggregate txns ---
agg = defaultdict(lambda: [D(0), 0])
cal_mismatch = 0
for r in txn:
    k = (r["BudgetMonth"], r["Item"]); agg[k][0] += D(r["Amount"]); agg[k][1] += 1
    yy, mm, dd = filenames.iso_date(r["Date"])
    if f"{yy}-{mm}" != r["BudgetMonth"]: cal_mismatch += 1

txn_months = sorted({r["BudgetMonth"] for r in txn})
rows = []
for bm in txn_months:
    items = {i for (b, i) in summ if b == bm} | {i for (b, i) in agg if b == bm}
    for item in sorted(items):
        g, planned, spent = summ.get((bm, item), ("", None, None))
        tsum, n = agg.get((bm, item), (None, 0))
        if spent is None:        status = "ITEM_NOT_IN_SUMMARY"
        elif tsum is None:
            status = "OK_NO_ACTIVITY" if spent == 0 else "NO_TXNS_BUT_SPENT"
        elif tsum == spent:      status = "OK"
        else:                    status = "MISMATCH"
        delta = (tsum or D(0)) - (spent or D(0))
        rows.append((bm, g, item, planned, spent, tsum, n, delta, status))

out = f"{ROOT}/reconciliation_new_rows.csv"
with open(out, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["BudgetMonth","Group","Item","Planned","Summary_Spent","Txn_Sum","Txn_Count","Delta(Txn-Summary)","Status"])
    w.writerows(rows)

newrows = f"{ROOT}/new_rows_with_budget_month.csv"
with open(newrows, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["Group","Item","Type","Date","Merchant","Amount","BudgetMonth","SourceFile"])
    for r in txn: w.writerow([r["Group"],r["Item"],r["Type"],"-".join(filenames.iso_date(r["Date"])),r["Merchant"],r["Amount"],r["BudgetMonth"],r["SourceFile"]])

c = Counter(s for *_, s in rows)
print(f"txn rows: {len(txn)}  calendar-month != budget-month: {cal_mismatch}")
print("status counts:", dict(c))
print()
for bm, g, item, planned, spent, tsum, n, delta, status in rows:
    if status in ("MISMATCH", "NO_TXNS_BUT_SPENT", "ITEM_NOT_IN_SUMMARY"):
        print(f"{bm}  {status:20s} {g:20s} {item:34s} summary={spent!s:>10} txn={tsum!s:>10} n={n:<3} delta={delta}")
bad = [r for r in rows if r[-1] not in ("OK", "OK_NO_ACTIVITY")]
print(f"\n{'CLEAN' if not bad else f'{len(bad)} item(s) do not reconcile'}")
sys.exit(1 if bad else 0)
