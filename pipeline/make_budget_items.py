#!/usr/bin/env python3 -I
"""Planned vs spent per envelope per budget month, from summaries/ + item_mapping.csv -> dash_data/budget_items.json"""
import csv, os, re, json
import sys as _sys; _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # filenames.py sits beside this script
import filenames   # noqa: E402
def norm(x): return re.sub(r"\s+"," ",x.replace(" "," ")).strip()
mp={norm(r["Item"]):r for r in csv.DictReader(open("item_mapping.csv"))}
rows=[]
for bm, fn in filenames.exports("summaries", "budget_items"):
    for r in csv.DictReader(open("summaries/"+fn,newline="",encoding="utf-8-sig")):
        it=norm(r["Item"]); M=mp[it]
        if M["Flow"] in ("exclude","zero","plug","verify","transfer"): continue
        planned=float(r["Planned"]); spent=float(r["Spent"])
        p,s=(planned,spent) if M["Flow"]=="income" else (planned,-spent)
        rows.append({"m":bm,"group":norm(r["Group"]),"item":it,"canonical":M["CanonicalItem"],"category":M["Category"],"flow":M["Flow"],"planned":round(p,2),"actual":round(s,2)})
os.makedirs("dash_data",exist_ok=True)
json.dump(rows,open("dash_data/budget_items.json","w"),separators=(",",":"))
print(len(rows),"rows")

# plan_suggestions and decisions (#155): both need the budget rows above, so they are emitted here rather than in make_datasets.py,
# which runs before this script. pipeline/decisions.py holds the rules; the app's check-in steps judge with the same functions.
import decisions as _dec   # noqa: E402  (beside this script)
RU = json.load(open("rules.json")) if os.path.exists("rules.json") else {}
def _rl(k, d): return RU.get(k) if RU.get(k) is not None else d
def w(name, data):
    json.dump(data, open(f"dash_data/{name}.json", "w"), separators=(",", ":")); print(f"{name:16s} {len(data):>5} rows")
w("plan_suggestions", _dec.plan_suggestions(rows, _rl("plan_keep_categories", ["Housing", "Debt", "Insurance"])))   # role default
_D = json.load(open("decisions.json")) if os.path.exists("decisions.json") else {"decisions": [], "checks": []}
_checks = {(c["decision_id"], c["month"]): c for c in _D["checks"]}
_built = {r["m"] for r in rows} | {bm for bm, _ in filenames.exports("summaries", "budget_items")}
_through = max(_built | {bm for bm, _ in filenames.exports("txns", "transactions")}, default=None)
_rows = _dec.judge(_D["decisions"], _checks, rows, _built, _through, _rl("decision_partly_over", _dec.PARTLY_OVER), _rl("decision_partly_share", _dec.PARTLY_SHARE))
w("decisions", _rows)
w("decision_summary", [{**_dec.keep_rate(_rows, _through), "in_force": len({r["id"] for r in _rows if r["m"] == _through})}] if _through else [])
