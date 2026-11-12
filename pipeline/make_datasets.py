#!/usr/bin/env python3 -I
"""Write dashboard dataset files (JSON row arrays) from dashboard_data.json + ledger.csv."""
import json, csv, os, collections
import sys as _sys; _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # filenames.py sits beside this script
import filenames   # noqa: E402
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
w("build_meta", [{"cpi_base_month": D["meta"]["cpi_base_month"], "cpi_base": D["meta"]["cpi_base"]}])   # the base month aggregate.py used for every *_real figure
w("cpi", [{"month":r["month"], "cpi":float(r["cpi"]), "source":r["source"]} for r in csv.DictReader(open("cpi_u.csv"))])

ex = D["exclusions"]
RU = json.load(open("rules.json")) if os.path.exists("rules.json") else {}
# kind: what the row is, so the Method tab can tell the story from data; detail: the rule window that produced it, if any
def xr(key, value, unit, kind, **extra): return {"key":key,"value":value,"unit":unit,"kind":kind, **extra}
rows = [xr(f"{item} rows excluded", n, "rows", "excluded_item", item=item, amount=ex.get("excluded_amounts", {}).get(item)) for item,n in sorted(ex["excluded_items"].items())] + [
        xr("Inter-item transfers tagged", ex["transfers_tagged"], "rows", "transfers_tagged"),
        xr("Transfers matched as pairs", ex["transfers_paired"], "rows", "transfers_paired"),
        xr("Balancing plugs dropped", ex["plugs"], "rows", "plugs", detail=f"through {RU['debt_plug_until']}" if RU.get("debt_plug_until") else ""),
        xr("Income-typed transfers removed from income", ex["income_typed_transfers_total"], "usd", "income_transfers"),
        xr("Family loan inflows removed from income", ex["family_loan_in"], "usd", "loan_in"),
        xr("Family loan repayments removed from spend", ex["family_loan_out"], "usd", "loan_out"),
        xr("Cash moved off-budget, unpaired", ex["offbudget_outflows_unpaired"], "usd", "offbudget", detail=f"since {RU['offbudget_since']}" if RU.get("offbudget_since") else ""),
        xr("Rows in totals", D["meta"]["rows_in_totals"], "rows", "rows_in_totals"),
        xr("Rows in ledger", D["meta"]["rows_total"], "rows", "rows_total"),
        xr("Budget months", D["meta"]["months"], "months", "months")]
w("exclusions", rows)
w("verify", [{"m":r["m"],"item":r["item"],"merchant":r["merchant"],"amount":r["amt"]} for r in ex["verify_rows"]])

# slush buckets: spend by canonical item for the catch-alls named in rules.json (catchall_items; none by default), by year
L = list(csv.DictReader(open("ledger.csv")))
CATCHALL = set(RU.get("catchall_items") or [])
slush = collections.defaultdict(float)
for r in L:
    if r["InTotals"]=="1" and r["CanonicalItem"] in CATCHALL and r["Flow"]=="expense":
        slush[(r["BudgetMonth"][:4], r["CanonicalItem"])] += -float(r["Amount"])
w("slush", [{"y":k[0],"item":k[1],"amount":round(v,2)} for k,v in sorted(slush.items())])

# balances (#143): month-end balances from balances.csv, with the months between estimated (debts) or carried (assets); balance_totals
# is the same by kind and type, without account names, for the review and MCP. Both are empty when accounts.csv is absent.
import balances as _bal   # noqa: E402  (beside this script, like filenames.py)
_debt_type = {r["CanonicalItem"]: (r.get("DebtType") or "").strip() or None for r in csv.DictReader(open("item_mapping.csv", newline=""))}
_accts = [{**a, "type": a["type"] or (_debt_type.get(a["debt_item"]) if a["kind"] == "debt" else None)} for a in _bal.read_accounts()]
_paid = collections.defaultdict(float)   # (canonical item, budget month) -> dollars paid, as the Debts tab counts them
for r in L:
    if r["InTotals"]=="1" and r["Flow"]=="debt":
        _paid[(r["CanonicalItem"], r["BudgetMonth"])] += -float(r["Amount"])
_last = max([r["BudgetMonth"] for r in L] + [m for (_, m) in _bal.read_balances()] or [None], key=lambda x: x or "")
_brows = _bal.series(_accts, _bal.read_balances(), _paid, _last)
w("balances", _brows)
w("balance_totals", _bal.totals(_brows))
# net_worth (#146): what the household did in a month is its income less living spend (net plus debt payments: paying down a loan is
# the household's doing; the interest in those payments shows up in "what happened")
_did = {m: mn[m]["net"] + mn[m]["debt"] for m in mn}
_cpi = {r["month"]: float(r["cpi"]) for r in csv.DictReader(open("cpi_u.csv"))}
w("net_worth", _bal.net_worth(_brows, _did, _cpi, D["meta"]["cpi_base"]))
# emergency_fund (#150): emergency money (asset accounts marked emergency) over the average monthly outgo (living spend plus debt
# payments) of the trailing twelve budget months, or as many as there are. null coverage: no emergency account, or no outgo yet.
_ems, _efr = sorted(mn), []
for i, m in enumerate(_ems):
    held = [r["amount"] for r in _brows if r["m"] == m and r["emergency"]]
    win = _ems[max(0, i - 11):i + 1]; outgo = sum(mn[x]["spend"] + mn[x]["debt"] for x in win) / len(win)
    money = round(sum(held), 2) if held else None
    _efr.append({"m": m, "emergency": money, "accounts": len(held), "unconfirmed": sum(1 for r in _brows if r["m"] == m and r["emergency"] and not r["confirmed"]),
                 "outgo": round(outgo, 2), "outgo_months": len(win), "months": round(money / outgo, 2) if money is not None and outgo > 0 else None})
for a, b in zip(_efr, _efr[1:]):
    b["change"] = round(b["months"] - a["months"], 2) if a["months"] is not None and b["months"] is not None else None
if _efr: _efr[0]["change"] = None
w("emergency_fund", _efr)


# what the Goals tab counts as giving, housing and savings (defaults as in build_ledger.RULES), and the catch-all list for its copy
def _rl(key, default): return RU.get(key) if RU.get(key) is not None else default
# plan_keep_categories (issue #162): the Last month tab's suggested plan keeps the current plan for these categories
w("goal_config", [{"giving_categories": _rl("goal_giving_categories", ["Giving"]), "housing_categories": _rl("goal_housing_categories", ["Housing", "Utilities"]),   # role default
                   "savings_groups": _rl("goal_savings_groups", []), "catchall_items": sorted(CATCHALL), "paycheck_min": _rl("paycheck_min", 500),
                   "plan_keep_categories": _rl("plan_keep_categories", ["Housing", "Debt", "Insurance"])}])   # role default

# reconciliation: every envelope whose transactions do not sum to its BudgetItems Spent (empty when the books are clean)
from decimal import Decimal as _D
def _files(folder, kind):
    for bm, fn in filenames.exports(folder, kind): yield bm, os.path.join(folder, fn)
_tx, _sp = collections.defaultdict(_D), {}
for _bm, _p in _files("txns", "transactions"):
    for r in csv.DictReader(open(_p, newline="", encoding="utf-8-sig")): _tx[(_bm, r["Item"])] += _D(r["Amount"])
for _bm, _p in _files("summaries", "budget_items"):
    for r in csv.DictReader(open(_p, newline="", encoding="utf-8-sig")): _sp[(_bm, r["Item"])] = _D(r["Spent"])
rec = []
for (_bm, _it) in sorted(set(_sp) | set(_tx)):
    s, t = _sp.get((_bm, _it)), _tx.get((_bm, _it))
    status = "ITEM_NOT_IN_SUMMARY" if s is None else "NO_TXNS_BUT_SPENT" if t is None and s != 0 else "OK" if (t or 0) == s else "MISMATCH"
    if status != "OK":
        rec.append({"m": _bm, "item": _it, "txn_sum": float(t or 0), "spent": None if s is None else float(s), "delta": float((t or 0) - (s or 0)), "status": status})
w("reconciliation", rec)

# debt_to_income (#145): debt payments and housing payments over gross monthly income (goals.gross_annual / 12), each month and over the
# trailing twelve, and the same payments over take-home (paychecks, as the Goals tab counts them). Housing is the goal_housing_categories'
# spending and debt payments plus any debt of type mortgage (#141), counted row by row so a mortgage in a housing category counts once.
# No gross stated: the ratios are null ("—"), never 0. Goals come from dash_data/goals.json (run_pipeline writes it); absent, no gross.
_goals = json.load(open("dash_data/goals.json")) if os.path.exists("dash_data/goals.json") else []
_gv = {g["key"]: g.get("value") for g in _goals}
_gross = float(_gv["gross_annual"]) / 12 if _gv.get("gross_annual") else None
_house_cats = set(_rl("goal_housing_categories", ["Housing", "Utilities"]))   # role default
_pay_items = set(_rl("paycheck_items", ["Paycheck"]))   # role default
_dm = collections.defaultdict(lambda: {"debt": 0.0, "housing": 0.0, "takehome": 0.0})
for r in L:
    if r["InTotals"] != "1":
        continue
    a, d = float(r["Amount"]), _dm[r["BudgetMonth"]]
    if r["Flow"] == "debt": d["debt"] += -a
    if (r["Flow"] in ("expense", "debt") and r["Category"] in _house_cats) or (r["Flow"] == "debt" and _debt_type.get(r["CanonicalItem"]) == "mortgage"):
        d["housing"] += -a
    if r["CanonicalItem"] in _pay_items: d["takehome"] += a
_share = lambda x, y: round(x / y, 4) if y else None
_dti, _ms = [], sorted({r["BudgetMonth"] for r in L})
for i, m in enumerate(_ms):
    d, win = _dm[m], _ms[max(0, i - 11):i + 1]
    t = {k: sum(_dm[x][k] for x in win) for k in ("debt", "housing", "takehome")}
    _dti.append({"m": m, "debt": round(d["debt"], 2), "housing": round(d["housing"], 2), "takehome": round(d["takehome"], 2), "gross": round(_gross, 2) if _gross else None,
                 "dti": _share(d["debt"], _gross), "housing_dti": _share(d["housing"], _gross),
                 "dti_takehome": _share(d["debt"], d["takehome"]), "housing_takehome": _share(d["housing"], d["takehome"]),
                 "months_12": len(win), "dti_12": _share(t["debt"], _gross * len(win) if _gross else None), "housing_dti_12": _share(t["housing"], _gross * len(win) if _gross else None),
                 "dti_takehome_12": _share(t["debt"], t["takehome"]), "housing_takehome_12": _share(t["housing"], t["takehome"])})
w("debt_to_income", _dti)
