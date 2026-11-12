#!/usr/bin/env python3 -I
"""Aggregate ledger.csv into dashboard-ready JSON + audit tables."""
import csv, json, collections, os
from datetime import date

L = [r for r in csv.DictReader(open("ledger.csv"))]
for r in L:
    r["amt"] = float(r["Amount"]); r["real"] = float(r["AmountReal"]); r["in"] = r["InTotals"] == "1"
T = [r for r in L if r["in"]]
RU = json.load(open("rules.json")) if os.path.exists("rules.json") else {}
PAYCHECK_MIN = float(RU.get("paycheck_min") if RU.get("paycheck_min") is not None else 500)   # same default as build_ledger.RULES
def _role(key, default): return set(RU.get(key) if RU.get(key) is not None else default)
# roles (issue #162): same defaults as build_ledger.RULES
PAYCHECK_ITEMS = _role("paycheck_items", ["Paycheck"])   # role default
RECURRING_CATEGORIES = _role("recurring_categories", ["Subscriptions", "Utilities", "Insurance"])   # role default
INCOME_GROUPS = _role("income_groups", ["Income"])   # role default
DEBT_GROUPS = _role("debt_groups", ["Debt"])   # role default
LOAN_CATEGORIES = _role("loan_categories", ["Family Loan"])   # role default
months = sorted({r["BudgetMonth"] for r in L})
years = sorted({m[:4] for m in months})

def agg(rows, key, val):
    d = collections.defaultdict(float)
    for r in rows: d[key(r)] += r[val]
    return d

# --- monthly series: income (positive, flow income), spend (expense, sign-flipped), debt payments ---
def series(val):
    inc = agg([r for r in T if r["Flow"]=="income"], lambda r:r["BudgetMonth"], val)
    exp = agg([r for r in T if r["Flow"]=="expense"], lambda r:r["BudgetMonth"], val)
    debt = agg([r for r in T if r["Flow"]=="debt"], lambda r:r["BudgetMonth"], val)
    return [{"m":m, "income":round(inc[m],2), "spend":round(-exp[m],2), "debt":round(-debt[m],2),
             "net":round(inc[m]+exp[m]+debt[m],2)} for m in months]
monthly = {"nominal": series("amt"), "real": series("real")}

# --- annual ---
def annual(val):
    out=[]
    for y in years:
        rows=[r for r in T if r["BudgetMonth"].startswith(y)]
        inc=sum(r[val] for r in rows if r["Flow"]=="income"); exp=-sum(r[val] for r in rows if r["Flow"]=="expense"); debt=-sum(r[val] for r in rows if r["Flow"]=="debt")
        n=len({r["BudgetMonth"] for r in rows})
        out.append({"y":y,"months":n,"income":round(inc,2),"spend":round(exp,2),"debt":round(debt,2),"net":round(inc-exp-debt,2),
                    "savings_rate": round((inc-exp-debt)/inc,4) if inc else None})
    return out
yearly = {"nominal": annual("amt"), "real": annual("real")}

# --- category by year & by month (expense + debt + taxes) ---
cats = sorted({r["Category"] for r in T if r["Flow"] in ("expense","debt")})
def cat_year(val):
    d=collections.defaultdict(lambda: collections.defaultdict(float))
    for r in T:
        if r["Flow"] in ("expense","debt"): d[r["BudgetMonth"][:4]][r["Category"]] += -r[val]
    return {y:{c:round(d[y][c],2) for c in cats} for y in years}
def cat_month(val):
    d=collections.defaultdict(lambda: collections.defaultdict(float))
    for r in T:
        if r["Flow"] in ("expense","debt"): d[r["BudgetMonth"]][r["Category"]] += -r[val]
    return {m:{c:round(d[m][c],2) for c in cats if d[m][c]} for m in months}
category = {"cats":cats, "by_year":{"nominal":cat_year("amt"),"real":cat_year("real")}, "by_month":{"nominal":cat_month("amt"),"real":cat_month("real")}}

# --- canonical item by year (for drill-down) ---
items = collections.defaultdict(lambda: collections.defaultdict(float))
for r in T:
    if r["Flow"] in ("expense","debt"): items[(r["Category"],r["CanonicalItem"])][r["BudgetMonth"][:4]] += -r["amt"]
item_year = [{"cat":k[0],"item":k[1], **{y:round(v[y],2) for y in years if v[y]}} for k,v in sorted(items.items())]

# --- earner / payer ---
earner = collections.defaultdict(lambda: collections.defaultdict(float)); payer = collections.defaultdict(lambda: collections.defaultdict(float))
for r in T:
    if r["Flow"]=="income": earner[r["BudgetMonth"][:4]][r["Earner"]] += r["amt"]
    if r["Flow"] in ("expense","debt"): payer[r["BudgetMonth"][:4]][r["Payer"]] += -r["amt"]
earner_payer = {"earner":{y:{k:round(v,2) for k,v in earner[y].items()} for y in years}, "payer":{y:{k:round(v,2) for k,v in payer[y].items()} for y in years}}

# --- employer by month (paychecks) ---
emp = collections.defaultdict(lambda: collections.defaultdict(float)); empn = collections.defaultdict(lambda: collections.defaultdict(int))
for r in T:
    if r["CanonicalItem"] in PAYCHECK_ITEMS:
        emp[r["BudgetMonth"]][r["Employer"] or "Unknown"] += r["amt"]
        if r["amt"] > PAYCHECK_MIN: empn[r["BudgetMonth"]][r["Employer"] or "Unknown"] += 1   # deposits; small ones are deductions
employer = {m:{k:{"amount":round(v,2),"n":empn[m][k]} for k,v in emp[m].items()} for m in months}

# --- debt payoff timeline (cumulative paid per debt item) ---
debt = collections.defaultdict(lambda: collections.defaultdict(float))
for r in T:
    if r["Flow"]=="debt": debt[r["CanonicalItem"]][r["BudgetMonth"]] += -r["amt"]
debts=[]
for k,v in debt.items():
    ms=sorted(v); debts.append({"item":k,"first":ms[0],"last":ms[-1],"total":round(sum(v.values()),2),"months":len(ms)})
debts.sort(key=lambda d:d["first"])

# --- recurring subscriptions: last 12 months monthly avg vs first 12 of the item ---
subs=collections.defaultdict(lambda: collections.defaultdict(float))
for r in T:
    if r["Category"] in RECURRING_CATEGORIES and r["Flow"]=="expense": subs[r["CanonicalItem"]][r["BudgetMonth"]] += -r["amt"]
recurring=[]
last12=months[-12:]
for k,v in subs.items():
    ms=sorted(v); cur=sum(v[m] for m in last12)/12
    recurring.append({"item":k,"first":ms[0],"last":ms[-1],"active":ms[-1]>=months[-3:][0],"monthly_now":round(cur,2),"lifetime":round(sum(v.values()),2)})
recurring.sort(key=lambda s:-s["monthly_now"])

# --- transfers & exclusions summary ---
RULESA = json.load(open("rules.json")) if os.path.exists("rules.json") else {}
OFFBUDGET_SINCE = RULESA.get("offbudget_since") or "0000-00"
excl = {
 "transfers_tagged": sum(1 for r in L if r["IsTransfer"]=="1"),
 "transfers_paired": sum(1 for r in L if r["TransferPair"]),
 "plugs": sum(1 for r in L if r["IsPlug"]=="1"),
 "income_typed_transfers_total": round(sum(r["amt"] for r in L if r["IsTransfer"]=="1" and r["Group"] in INCOME_GROUPS and r["amt"]>0),2),
 "family_loan_in": round(sum(r["amt"] for r in L if r["Category"] in LOAN_CATEGORIES and r["amt"]>0),2),
 "family_loan_out": round(-sum(r["amt"] for r in L if r["Category"] in LOAN_CATEGORIES and r["amt"]<0),2),
 "offbudget_outflows_unpaired": round(-sum(r["amt"] for r in L if r["IsTransfer"]=="1" and not r["TransferPair"] and r["amt"]<0 and r["Group"] not in DEBT_GROUPS and r["BudgetMonth"]>=OFFBUDGET_SINCE),2),
 "verify_rows": [{"m":r["BudgetMonth"],"item":r["Item"],"merchant":r["Merchant"],"amt":r["amt"]} for r in L if r["Flow"]=="verify"],
 "excluded_items": json.load(open("excluded.json")) if os.path.exists("excluded.json") else {},
 "excluded_amounts": json.load(open("excluded_amounts.json")) if os.path.exists("excluded_amounts.json") else {},
}

# --- life events (dated markers) ---
events = json.load(open("events.json")) if os.path.exists("events.json") else []

cpi = {r["month"]:r for r in csv.DictReader(open("cpi_u.csv"))}
CPI_BASE_MONTH = RULESA.get("cpi_base_month") or max(cpi)
meta = {"built": date.today().isoformat(), "months": len(months), "first": months[0], "last": months[-1],
        "rows_total": len(L), "rows_in_totals": len(T), "cpi_base_month": CPI_BASE_MONTH, "cpi_base": float(cpi[CPI_BASE_MONTH]["cpi"]),
        "cpi_filled": [k for k,v in cpi.items() if v["source"]!="FRED CPIAUCSL"]}

data = {"meta":meta,"monthly":monthly,"yearly":yearly,"category":category,"item_year":item_year,"earner_payer":earner_payer,
        "employer":employer,"debts":debts,"recurring":recurring,"exclusions":excl,"events":events}
json.dump(data, open("dashboard_data.json","w"), separators=(",",":"))
print(json.dumps(yearly["nominal"], indent=0)[:1500])
print("debts:", [(d["item"],d["first"],d["last"],d["total"]) for d in debts])
print("exclusions:", {k:v for k,v in excl.items() if k!="verify_rows"})
import os; print("json bytes:", os.path.getsize("dashboard_data.json"))
