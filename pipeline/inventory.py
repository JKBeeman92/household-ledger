#!/usr/bin/env python3 -I
"""Item inventory across all staged months: first/last budget month, months active, txn count, net."""
import csv, re, os, collections
import sys as _sys; _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # filenames.py sits beside this script
import filenames   # noqa: E402
def norm(x): return re.sub(r'\s+',' ',x.replace('\u2006',' ')).strip()
inv = collections.defaultdict(lambda: {"first":"9999","last":"0000","months":set(),"n":0,"net":0.0,"types":set()})
for bm, fn in filenames.exports("txns", "transactions"):
    with open(os.path.join("txns",fn), newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k=(norm(r["Group"]),norm(r["Item"])); d=inv[k]
            d["first"]=min(d["first"],bm); d["last"]=max(d["last"],bm); d["months"].add(bm)
            d["n"]+=1; d["net"]+=float(r["Amount"]); d["types"].add(r["Type"])
# summary-only items (never had a txn)
summ=collections.defaultdict(lambda: {"first":"9999","last":"0000","months":set()})
for bm, fn in filenames.exports("summaries", "budget_items"):
    with open(os.path.join("summaries",fn), newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k=(norm(r["Group"]),norm(r["Item"])); d=summ[k]
            d["first"]=min(d["first"],bm); d["last"]=max(d["last"],bm); d["months"].add(bm)
with open("item_inventory.csv","w",newline="") as out:
    w=csv.writer(out); w.writerow(["Group","Item","Types","FirstBM","LastBM","MonthsWithTxns","MonthsInBudget","TxnCount","NetAmount"])
    for k in sorted(set(inv)|set(summ), key=lambda k:(summ.get(k,inv.get(k))["first"],k)):
        d=inv.get(k); s=summ.get(k)
        w.writerow([k[0],k[1],"/".join(sorted(d["types"])) if d else "",
                    s["first"] if s else d["first"], s["last"] if s else d["last"],
                    len(d["months"]) if d else 0, len(s["months"]) if s else 0,
                    d["n"] if d else 0, f"{d['net']:.2f}" if d else "0.00"])
print(len(set(inv)|set(summ)),"distinct (Group,Item) pairs;", len({k[1] for k in set(inv)|set(summ)}),"distinct Item names")
