#!/usr/bin/env python3 -I
"""Generate the synthetic sample household: 18 months of EveryDollar-shaped exports plus the seed files.

Deterministic (fixed seed, no clock, no environment). Everything here is invented.
    python -I generate.py [output-dir]      # default: the directory this script lives in
"""
import csv, json, random, sys
from datetime import date, timedelta
from pathlib import Path

SEED = 8
HEX = "5ca1ab1e"
FIRST = (2025, 1)
MONTHS = 18
ACCOUNT = "Bywater Checking"

# group, item, canonical, category, flow, planned $, merchants, (min, max) transactions per month, spread of actual vs planned
ITEMS = [
    ("Income", "Rosie Paycheck", "Paycheck", "Income", "income", 0, ["HOBBITON MILL PAYROLL"], None, None),
    ("Income", "Sam Contract Income", "Contract Income", "Income", "income", 1900, ["Gaffer's Garden Works", "Sackville Estates"], (1, 3), (0.8, 1.25)),
    ("Giving", "Charity", "Generosity", "Giving", "expense", 380, ["Bywater Parish Alms"], (1, 2), (1.0, 1.0)),
    ("Savings", "Emergency Fund", "Emergency Fund", "Savings", "expense", 300, ["Green Hills Credit Union"], (1, 1), (1.0, 1.0)),
    ("Savings", "Holiday", "Holiday", "Savings", "expense", 100, ["Bywater Market", "Hobbiton Toys"], (0, 0), (1.0, 1.0)),
    ("Housing", "Rent", "Rent", "Housing", "expense", 1450, ["Bywater Property Co"], (1, 1), (1.0, 1.0)),
    ("Utilities", "Electric", "Electric", "Utilities", "expense", 95, ["SHIRE POWER & LIGHT"], (1, 1), (0.85, 1.2)),
    ("Utilities", "Water", "Water", "Utilities", "expense", 48, ["Bywater Water Works"], (1, 1), (0.9, 1.1)),
    ("Utilities", "Internet", "Internet", "Utilities", "expense", 65, ["Hobbit Hole Fibre"], (1, 1), (1.0, 1.0)),
    ("Insurance", "Renters Insurance", "Renters Insurance", "Insurance", "expense", 22, ["Brandybuck Mutual"], (1, 1), (1.0, 1.0)),
    ("Insurance", "Car Insurance", "Car Insurance", "Insurance", "expense", 118, ["Brandybuck Mutual"], (1, 1), (1.0, 1.0)),
    ("Food", "Groceries", "Groceries", "Food", "expense", 720, ["BYWATER MARKET #0412", "Farmer Maggot's Farm Stand", "Whole Foods"], (7, 11), (0.85, 1.15)),
    ("Food", "Eating Out", "Eating Out", "Food", "expense", 160, ["GREEN DRAGON INN 8812", "Prancing Pony Takeaway", "Chipotle"], (3, 6), (0.7, 1.4)),
    ("Transportation", "Fuel", "Fuel", "Transportation", "expense", 140, ["Shell", "Shire Fuel Stop"], (3, 5), (0.8, 1.2)),
    ("Transportation", "Car Maintenance", "Car Maintenance", "Transportation", "expense", 60, ["Gamgee & Sons Garage"], (0, 1), (1.0, 3.0)),
    ("Health", "Medicine", "Medicine", "Health", "expense", 55, ["Healing Hands Apothecary", "CVS"], (1, 2), (0.5, 1.5)),
    ("Subscriptions", "Streaming", "Streaming", "Subscriptions", "expense", 32, ["Netflix", "Spotify"], (2, 2), (1.0, 1.0)),
    ("Household", "Household Supplies", "Household Supplies", "Household", "expense", 85, ["Walmart", "Home Depot", "Bywater Hardware"], (1, 3), (0.7, 1.3)),
    ("Personal", "Rosie Fun Money", "Rosie Fun Money", "Personal", "expense", 120, ["Amazon", "Starbucks", "Bywater Books"], (2, 4), (0.7, 1.2)),
    ("Personal", "Sam Fun Money", "Sam Fun Money", "Personal", "expense", 120, ["Amazon", "Green Dragon Inn", "Bywater Seeds & Bulbs"], (2, 4), (0.7, 1.2)),
    ("Fun", "Date Night", "Date Night", "Fun", "expense", 100, ["Green Dragon Inn", "Hobbiton Picture House"], (0, 2), (0.6, 1.3)),
    ("Debt", "Student Loan", "Student Loan", "Debt", "debt", 275, ["Lembas Loan Servicing"], (1, 1), (1.0, 1.0)),
    ("Other", "Venmo Pass-Through", "Venmo Pass-Through", "Other", "exclude", 0, ["Venmo"], (0, 2), (1.0, 1.0)),
]
FIXED_DAY = {"Rent": 1, "Internet": 6, "Renters Insurance": 9, "Car Insurance": 12, "Student Loan": 15, "Emergency Fund": 4, "Electric": 18, "Water": 21, "Charity": 5}
SEASON = {"Electric": {12: 1.35, 1: 1.45, 2: 1.35, 7: 1.15, 8: 1.15}}
PAIR_MONTHS = {"2025-05": 4000, "2025-11": 6000, "2026-02": 4500, "2026-05": 5000}   # cents moved Date Night -> Holiday
EXTRA = {("2025-12", "Holiday"): (["Hobbiton Toys", "Bywater Market", "Amazon"], 38000), ("2025-12", "Charity"): (["Bywater Parish Alms"], 20000)}
PAY_START, PAY_EVERY = date(2025, 1, 3), 14
RENT_UP = ("2025-10", 1525)
RAISE = ("2025-07", 1960)
NET_PAY = 1880

MAPPING_NOTES = {"Paycheck": "Rosie, paid every two weeks", "Contract Income": "Sam, garden contracts",
                 "Venmo Pass-Through": "money moved for friends, not part of the household budget"}
EVENTS = [("2025-03", "Sam takes on garden contracts"), ("2025-07", "Rosie promoted at Hobbiton Mill"),
          ("2025-10", "Moved to a bigger hole, rent goes up"), ("2026-02", "Elanor is born")]
RULES = {
    "people": ["Rosie", "Sam"],
    "income_by_person": {"Rosie": ["Paycheck"], "Sam": ["Contract Income"]},
    "payer_prefix_by_person": {"Rosie": ["Rosie "], "Sam": ["Sam "]},
    "employers": [{"match": "hobbiton mill", "name": "Hobbiton Mill"}],
    "merchant_rules": [["green dragon", "Green Dragon Inn"], ["bywater market", "Bywater Market"], ["shire power", "Shire Power & Light"]],
    "joy_group": "Personal",
    "housing_categories": ["Housing", "Utilities"],
}
GOALS = [
    {"key": "gross_annual", "value": 78000, "unit": "usd", "label": "Gross salary, annual", "note": "Invented for the sample household."},
    {"key": "pay_frequency", "value": 26, "unit": "paychecks/yr", "label": "Pay frequency", "note": "26 = every two weeks; two or three months carry three paychecks."},
    {"key": "giving_floor", "value": 0.05, "unit": "share of gross", "label": "Giving floor", "note": "Never below this share of gross."},
    {"key": "giving_target", "value": 0.07, "unit": "share of gross", "label": "Giving target", "note": "Work toward this share of gross."},
    {"key": "joy_target", "value": 0.04, "unit": "share of gross", "label": "Joy savings target", "note": "Share of gross planned into the joy-savings group (rules.joy_group)."},
    {"key": "housing_cap", "value": 0.35, "unit": "share of take-home", "label": "Housing + utilities cap", "note": "Housing categories (rules.housing_categories) over net paychecks."},
    {"key": "investing_per_paycheck", "value": 150, "unit": "usd", "label": "Investing per paycheck", "note": "Deposited outside the budget app; not in the exports."},
]
HOUSEHOLD = {"name": "Sample Household", "title": "Sample Household Ledger", "currency": "USD"}


def money(c): return f"{'-' if c < 0 else ''}{abs(c) // 100}.{abs(c) % 100:02d}"
def split(rng, total, n):
    w = [rng.randint(40, 100) for _ in range(n)]; s = sum(w)
    parts = [total * x // s for x in w[:-1]]
    return parts + [total - sum(parts)]


def month_list():
    y, m = FIRST
    for _ in range(MONTHS):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def pay_dates(y, m):
    d, out = PAY_START, []
    while d.year * 12 + d.month <= y * 12 + m:
        if (d.year, d.month) == (y, m): out.append(d)
        d += timedelta(days=PAY_EVERY)
    return out


def build_month(rng, y, m):
    bm = f"{y}-{m:02d}"; last = 28
    txns, planned = [], {}   # txns: (day, group, item, type, merchant, cents, note)
    for g, item, canon, cat, flow, plan, merch, ntx, spread in ITEMS:
        base = plan * 100
        if item == "Rosie Paycheck":
            days = pay_dates(y, m); net = (RAISE[1] if bm >= RAISE[0] else NET_PAY) * 100
            planned[item] = net * len(days)
            for d in days: txns.append((d.day, g, item, "Income", merch[0], net, "three-paycheck month" if len(days) == 3 else ""))
            continue
        if item == "Rent" and bm >= RENT_UP[0]: base = RENT_UP[1] * 100
        planned[item] = base
        if (bm, item) in EXTRA:
            ms, tot = EXTRA[(bm, item)]
            planned[item] = tot if item == "Holiday" else base + tot
            for part in split(rng, tot, len(ms)):
                txns.append((rng.randint(3, last), g, item, "Expense", ms[0] if len(ms) == 1 else rng.choice(ms), -part, "holiday"))
            if item == "Charity": txns.append((FIXED_DAY[item], g, item, "Expense", merch[0], -base, ""))
            continue
        n = rng.randint(*ntx)
        if n == 0: continue
        if flow == "exclude":
            amt = rng.randint(1500, 6000)
            txns.append((rng.randint(2, last), g, item, "Expense", merch[0], -amt, "dinner split"))
            txns.append((rng.randint(2, last), g, item, "Income", merch[0], amt, "paid back"))
            continue
        total = int(base * SEASON.get(item, {}).get(m, 1.0) * rng.uniform(*spread)) // 100 * 100 + rng.randint(0, 99) if spread != (1.0, 1.0) else base
        sign = 1 if flow == "income" else -1
        typ = "Income" if flow == "income" else "Expense"
        for k, part in enumerate(split(rng, total, n)):
            day = FIXED_DAY.get(item, rng.randint(1, last)) if n == 1 else rng.randint(1, last)
            txns.append((day, g, item, typ, merch[k % len(merch)] if item == "Streaming" else rng.choice(merch), sign * part, ""))
    if bm in PAIR_MONTHS:
        c = PAIR_MONTHS[bm]
        txns.append((20, "Fun", "Date Night", "Expense", "Transfer to Holiday", -c, "covered the toy fund"))
        txns.append((20, "Savings", "Holiday", "Expense", "Transfer from Date Night", c, "covered the toy fund"))
    txns.sort(key=lambda t: (t[0], t[2], t[5]))
    return bm, txns, planned


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    for y, m in month_list():
        bm, txns, planned = build_month(rng, y, m)
        with open(out / f"{HEX}-{m:02d}-{y}-EveryDollar-Transactions.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["Group", "Item", "Type", "Date", "Merchant", "Account", "Amount", "Note"])
            for d, g, item, typ, merch, c, note in txns: w.writerow([g, item, typ, f"{m:02d}/{d:02d}/{y}", merch, ACCOUNT, money(c), note])
        spent = {}
        for d, g, item, typ, merch, c, note in txns: spent[item] = spent.get(item, 0) + c
        with open(out / f"{HEX}-{m:02d}-{y}-EveryDollar-BudgetItems.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["Group", "Item", "Planned", "Spent", "Remaining"])
            for g, item, canon, cat, flow, *_ in ITEMS:
                p, s = planned.get(item, 0), spent.get(item, 0)
                w.writerow([g, item, money(p), money(s), money(p - s if flow == "income" else p + s)])
    with open(out / "item_mapping.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note"])
        for g, item, canon, cat, flow, *_ in ITEMS: w.writerow([item, canon, cat, flow, MAPPING_NOTES.get(canon, "")])
    for name, obj in [("goals.json", GOALS), ("events.json", [{"m": m, "label": l} for m, l in EVENTS]), ("rules.json", RULES), ("household.json", HOUSEHOLD)]:
        (out / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent)
