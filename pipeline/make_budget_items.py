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
# yearly bills (#151): detected from the same budget rows, merged with the household's list; their next due month joins coming_up
import yearly_bills as _yb, csv as _csv   # noqa: E402
_stored = list(_csv.DictReader(open("yearly_bills.csv", newline=""))) if os.path.exists("yearly_bills.csv") else []
_acct = {}
if os.path.exists("dash_data/balances.json"):   # make_datasets.py ran first: the latest balance per account
    for b in json.load(open("dash_data/balances.json")): _acct[b["account"]] = b["amount"]
_names = sorted({r["canonical"] for r in rows})
_bills = _yb.bills(rows, _stored, _through, {r["m"] for r in rows}, _names, _acct, int(_rl("yearly_bill_tolerance", _yb.TOLERANCE)), int(_rl("yearly_bill_min_years", _yb.MIN_YEARS)))
w("yearly_bills", _bills)
if os.path.exists("dash_data/coming_up.json"):
    _cu = [r for r in json.load(open("dash_data/coming_up.json")) if r["kind"] != "yearly_bill"] + _yb.coming_up_rows(_bills)
    _cu.sort(key=lambda r: (r["m"], r["kind"], r["label"])); w("coming_up", _cu)
# planned purchases (#158): each dated goal's saving against its target, from the same budget rows (an envelope) or the balances (an
# account); an open one joins coming_up at its target month
import purchases as _pp   # noqa: E402
_glist = json.load(open("dash_data/goals.json")) if os.path.exists("dash_data/goals.json") else []
_brs = json.load(open("dash_data/balances.json")) if os.path.exists("dash_data/balances.json") else []
_pps = _pp.progress(_glist, rows, _through, _brs)
w("planned_purchases", _pps)
if os.path.exists("dash_data/coming_up.json"):
    _cu = [r for r in json.load(open("dash_data/coming_up.json")) if r["kind"] != "goal"] + _pp.coming_up_rows(_pps)
    _cu.sort(key=lambda r: (r["m"], r["kind"], r["label"])); w("coming_up", _cu)
# estimated tax (#153): the tax year's contract income against what is set aside, and the deadlines into coming_up
import tax as _tax   # noqa: E402
_goals = {g["key"]: g.get("value") for g in json.load(open("dash_data/goals.json"))} if os.path.exists("dash_data/goals.json") else {}
_pay = json.load(open("tax_payments.json")) if os.path.exists("tax_payments.json") else []
_ts = _tax.summary(rows, _rl("contract_income_items", []), _rl("tax_set_aside_items", []),
                   float(_goals["tax_set_aside_pct"]) if _goals.get("tax_set_aside_pct") is not None else None, _rl("tax_deadlines", []), _pay, _through, _acct)
w("tax", [_ts] if _ts else [])
if _ts and os.path.exists("dash_data/coming_up.json"):
    _cu = [r for r in json.load(open("dash_data/coming_up.json")) if r["kind"] != "tax_deadline"] + _tax.deadline_rows(_rl("tax_deadlines", []), _through, _ts["quarter_expected"])
    _cu.sort(key=lambda r: (r["m"], r["kind"], r["label"])); w("coming_up", _cu)
# quarter_review (#147): the latest quarter against the one before and a year before, and a look at the next; last, so that its
# look ahead sees coming_up with the yearly bills and tax deadlines in it
import quarters as _qr   # noqa: E402
def _j(n): return json.load(open(f"dash_data/{n}.json")) if os.path.exists(f"dash_data/{n}.json") else []
_mon = _j("monthly")
if _mon:
    _ys = int(_rl("year_start_month", 1))
    _qq = _qr.quarter_of(max(r["m"] for r in _mon), _ys)[0]
    _qrev = _qr.compare(_qq, _j("quarterly"), _j("cat_quarter"), _j("net_worth"), _j("balance_totals"), _j("coming_up"), _mon, _ys,
                        set_apart=set(_rl("debt_groups", ["Debt"])) | set(_rl("goal_savings_groups", [])))   # movers are living spend; role default
    w("quarter_review", [_qrev] if _qrev else [])
else:
    w("quarter_review", [])
w("decision_summary", [{**_dec.keep_rate(_rows, _through), "in_force": len({r["id"] for r in _rows if r["m"] == _through})}] if _through else [])
# a look ahead (#148): where the current pace leads in 3, 12 and 36 months. Last, so that coming_up and planned_purchases are final.
import look_ahead as _la   # noqa: E402
_g = _rl("assumed_growth_pct", None)
w("look_ahead", _la.look_ahead(_j("monthly"), _j("payoff"), _j("net_worth"), _j("balances"), _j("planned_purchases"), _j("coming_up"),
                                _through, float(_g) if _g is not None else None))
# the year in review (#157): the budget year of the latest month against the year before, quoting the look ahead's twelve months
import years as _yr   # noqa: E402
_yrev = _yr.review(_j("monthly"), _j("cat_month"), _j("net_worth"), _j("balances"), _rows, _j("events"), _j("look_ahead"), _through,
                   int(_rl("year_start_month", 1)), set(_rl("debt_groups", ["Debt"])) | set(_rl("goal_savings_groups", [])))   # role default
w("year_review", [_yrev] if _yrev else [])
