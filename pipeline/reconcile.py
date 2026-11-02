import csv, re as _re
def norm(x): return _re.sub(r'\s+',' ',x.replace('\u2006',' ')).strip(), glob, os, re, sys
from collections import defaultdict
from decimal import Decimal as D

ROOT = "/home/claude/fin"
rx = re.compile(r"^[0-9a-f]+-(\d\d)-(\d{4})-EveryDollar-(Transactions|BudgetItems)\.csv$")

def bm_of(fn):
    m = rx.match(os.path.basename(fn)); return f"{m.group(2)}-{m.group(1)}"

# --- transactions with Budget Month from filename ---
txn = []
for f in sorted(glob.glob(f"{ROOT}/txns/*.csv")):
    bm = bm_of(f)
    with open(f, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            r["BudgetMonth"] = bm; r["SourceFile"] = os.path.basename(f)
            txn.append(r)

# --- summaries ---
summ = {}  # (bm, item) -> (group, planned, spent)
for f in sorted(glob.glob(f"{ROOT}/summaries/*.csv")):
    bm = bm_of(f)
    with open(f, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            summ[(bm, r["Item"])] = (r["Group"], D(r["Planned"]), D(r["Spent"]))

# --- aggregate txns ---
agg = defaultdict(lambda: [D(0), 0])
cal_mismatch = 0
for r in txn:
    k = (r["BudgetMonth"], r["Item"]); agg[k][0] += D(r["Amount"]); agg[k][1] += 1
    mm, dd, yy = r["Date"].split("/")
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
    for r in txn: w.writerow([r["Group"],r["Item"],r["Type"],r["Date"],r["Merchant"],r["Amount"],r["BudgetMonth"],r["SourceFile"]])

from collections import Counter
c = Counter(s for *_, s in rows)
print(f"txn rows: {len(txn)}  calendar-month != budget-month: {cal_mismatch}")
print("status counts:", dict(c))
print()
for bm, g, item, planned, spent, tsum, n, delta, status in rows:
    if status in ("MISMATCH", "NO_TXNS_BUT_SPENT", "ITEM_NOT_IN_SUMMARY"):
        print(f"{bm}  {status:20s} {g:20s} {item:34s} summary={spent!s:>10} txn={tsum!s:>10} n={n:<3} delta={delta}")
