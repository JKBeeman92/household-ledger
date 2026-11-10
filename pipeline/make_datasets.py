#!/usr/bin/env python3 -I
"""Write dashboard dataset files (JSON row arrays) from dashboard_data.json + ledger.csv."""
import json, csv, os, collections
D = json.load(open("dashboard_data.json"))
os.makedirs("dash_data", exist_ok=True)
def w(name, rows):
    json.dump(rows, open(f"dash_data/{name}.json","w"), separators=(",",":"))
    print(f"{name:16s} {len(rows):>5} rows")

# monthly: nominal + real side by side
mn = {r["m"]:r for r in D["monthly"]["nominal"]}; mr = {r["m"]:r for r in D["monthly"]["real"]}
w("monthly", [{"m":m, "income":mn[m]["income"], "spend":mn[m]["spend"], "debt":mn[m]["debt"], "net":mn[m]["net"],
               "income_real":mr[m]["income_real"] if "income_real" in mr[m] else mr[m]["income"],
               "spend_real":mr[m]["spend"], "debt_real":mr[m]["debt"], "net_real":mr[m]["net"]} for m in mn])

yn = {r["y"]:r for r in D["yearly"]["nominal"]}; yr = {r["y"]:r for r in D["yearly"]["real"]}
w("yearly", [{"y":y, "months":yn[y]["months"], "income":yn[y]["income"], "spend":yn[y]["spend"], "debt":yn[y]["debt"], "net":yn[y]["net"],
              "savings_rate":yn[y]["savings_rate"], "income_real":yr[y]["income"], "spend_real":yr[y]["spend"], "debt_real":yr[y]["debt"],
              "net_real":yr[y]["net"]} for y in yn])

cy = D["category"]["by_year"]
w("cat_year", [{"y":y, "category":c, "amount":cy["nominal"][y][c], "amount_real":cy["real"][y][c]}
               for y in cy["nominal"] for c in cy["nominal"][y] if cy["nominal"][y][c] or cy["real"][y][c]])
cm = D["category"]["by_month"]
w("cat_month", [{"m":m, "category":c, "amount":v, "amount_real":cm["real"][m].get(c,0)} for m in cm["nominal"] for c,v in cm["nominal"][m].items()])

w("item_year", [{"y":k, "category":r["cat"], "item":r["item"], "amount":v} for r in D["item_year"] for k,v in r.items() if k not in ("cat","item")])

ep = D["earner_payer"]
w("people", [{"y":y, "role":"earner", "who":k, "amount":v} for y in ep["earner"] for k,v in ep["earner"][y].items()] +
            [{"y":y, "role":"payer", "who":k, "amount":v} for y in ep["payer"] for k,v in ep["payer"][y].items()])

w("employer", [{"m":m, "employer":k, "amount":v["amount"], "n":v["n"]} for m in D["employer"] for k,v in D["employer"][m].items()])
w("debts", D["debts"])
w("recurring", D["recurring"])
w("events", D["events"])
w("cpi", [{"month":r["month"], "cpi":float(r["cpi"]), "source":r["source"]} for r in csv.DictReader(open("cpi_u.csv"))])

ex = D["exclusions"]
rows = [{"key":f"{item} rows excluded","value":n,"unit":"rows"} for item,n in sorted(ex["excluded_items"].items())] + [
        {"key":"Inter-item transfers tagged","value":ex["transfers_tagged"],"unit":"rows"},
        {"key":"Transfers matched as pairs","value":ex["transfers_paired"],"unit":"rows"},
        {"key":"Balancing plugs dropped (2018)","value":ex["plugs"],"unit":"rows"},
        {"key":"Income-typed transfers removed from income","value":ex["income_typed_transfers_total"],"unit":"usd"},
        {"key":"Family loan inflows removed from income","value":ex["family_loan_in"],"unit":"usd"},
        {"key":"Family loan repayments removed from spend","value":ex["family_loan_out"],"unit":"usd"},
        {"key":"Cash moved off-budget, unpaired (2022+)","value":ex["offbudget_outflows_unpaired"],"unit":"usd"},
        {"key":"Rows in totals","value":D["meta"]["rows_in_totals"],"unit":"rows"},
        {"key":"Rows in ledger","value":D["meta"]["rows_total"],"unit":"rows"},
        {"key":"Budget months","value":D["meta"]["months"],"unit":"months"}]
w("exclusions", rows)
w("verify", [{"m":r["m"],"item":r["item"],"merchant":r["merchant"],"amount":r["amt"]} for r in ex["verify_rows"]])

# slush buckets: spend by canonical item for the known catch-alls, by year
L = list(csv.DictReader(open("ledger.csv")))
slush = collections.defaultdict(float)
for r in L:
    if r["InTotals"]=="1" and r["CanonicalItem"] in ("Long Term Savings (slush)","Taxes","Generosity","Emergency Fund") and r["Flow"]=="expense":
        slush[(r["BudgetMonth"][:4], r["CanonicalItem"])] += -float(r["Amount"])
w("slush", [{"y":k[0],"item":k[1],"amount":round(v,2)} for k,v in sorted(slush.items())])

# reconciliation: every envelope whose transactions do not sum to its BudgetItems Spent (empty when the books are clean)
from decimal import Decimal as _D
_rx = __import__("re").compile(r"^[0-9a-f]+-(\d\d)-(\d{4})-EveryDollar-(Transactions|BudgetItems)\.csv$")
def _files(folder, kind):
    for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        m = _rx.match(fn)
        if m and m.group(3) == kind: yield f"{m.group(2)}-{m.group(1)}", os.path.join(folder, fn)
_tx, _sp = collections.defaultdict(_D), {}
for _bm, _p in _files("txns", "Transactions"):
    for r in csv.DictReader(open(_p, newline="", encoding="utf-8-sig")): _tx[(_bm, r["Item"])] += _D(r["Amount"])
for _bm, _p in _files("summaries", "BudgetItems"):
    for r in csv.DictReader(open(_p, newline="", encoding="utf-8-sig")): _sp[(_bm, r["Item"])] = _D(r["Spent"])
rec = []
for (_bm, _it) in sorted(set(_sp) | set(_tx)):
    s, t = _sp.get((_bm, _it)), _tx.get((_bm, _it))
    status = "ITEM_NOT_IN_SUMMARY" if s is None else "NO_TXNS_BUT_SPENT" if t is None and s != 0 else "OK" if (t or 0) == s else "MISMATCH"
    if status != "OK":
        rec.append({"m": _bm, "item": _it, "txn_sum": float(t or 0), "spent": None if s is None else float(s), "delta": float((t or 0) - (s or 0)), "status": status})
w("reconciliation", rec)
