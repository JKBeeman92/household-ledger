#!/usr/bin/env python3 -I
"""Generate the synthetic sample household: 18 months of exports plus the seed files. The last month is in the internal format
(YYYY-MM-transactions.csv / YYYY-MM-budget.csv, ISO dates; pipeline/filenames.py), the others as EveryDollar names its exports,
so a first run exercises both (#161).

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
    ("Food", "Groceries", "Groceries", "Food", "expense", 720, ["BYWATER MARKET #0412", "Farmer Maggot's Farm Stand", "Overhill Greengrocer"], (7, 11), (0.85, 1.15)),
    ("Food", "Eating Out", "Eating Out", "Food", "expense", 160, ["GREEN DRAGON INN 8812", "Prancing Pony Takeaway", "Michel Delving Kitchen"], (3, 6), (0.7, 1.4)),
    ("Transportation", "Fuel", "Fuel", "Transportation", "expense", 140, ["Frogmorton Fuel", "Shire Fuel Stop"], (3, 5), (0.8, 1.2)),
    ("Transportation", "Car Maintenance", "Car Maintenance", "Transportation", "expense", 60, ["Gamgee & Sons Garage"], (0, 1), (1.0, 3.0)),
    ("Health", "Medicine", "Medicine", "Health", "expense", 55, ["Healing Hands Apothecary", "Michel Delving Chemist"], (1, 2), (0.5, 1.5)),
    ("Subscriptions", "Streaming", "Streaming", "Subscriptions", "expense", 32, ["Hobbiton Picture Stream", "Bywater Songbook"], (2, 2), (1.0, 1.0)),
    ("Household", "Household Supplies", "Household Supplies", "Household", "expense", 85, ["Michel Delving Emporium", "Tuckborough Timber", "Bywater Hardware"], (1, 3), (0.7, 1.3)),
    ("Personal", "Rosie Fun Money", "Rosie Fun Money", "Personal", "expense", 120, ["Westfarthing Mail Order", "Tea and Biscuits Cafe", "Bywater Books"], (2, 4), (0.7, 1.2)),
    ("Personal", "Sam Fun Money", "Sam Fun Money", "Personal", "expense", 120, ["Westfarthing Mail Order", "Green Dragon Inn", "Bywater Seeds & Bulbs"], (2, 4), (0.7, 1.2)),
    ("Fun", "Date Night", "Date Night", "Fun", "expense", 100, ["Green Dragon Inn", "Hobbiton Picture House"], (0, 2), (0.6, 1.3)),
    ("Debt", "Student Loan", "Student Loan", "Debt", "debt", 275, ["Lembas Loan Servicing"], (1, 1), (1.0, 1.0)),
    ("Other", "Venmo Pass-Through", "Venmo Pass-Through", "Other", "exclude", 0, ["Venmo"], (0, 2), (1.0, 1.0)),
]
DEBT_TYPES = {"Student Loan": "student"}   # the mapping's optional DebtType column (#141)
FIXED_DAY = {"Rent": 1, "Internet": 6, "Renters Insurance": 9, "Car Insurance": 12, "Student Loan": 15, "Emergency Fund": 4, "Electric": 18, "Water": 21, "Charity": 5}
SEASON = {"Electric": {12: 1.35, 1: 1.45, 2: 1.35, 7: 1.15, 8: 1.15}}
PAIR_MONTHS = {"2025-05": 4000, "2025-11": 6000, "2026-02": 4500, "2026-05": 5000}   # cents moved Date Night -> Holiday
EXTRA = {("2025-12", "Holiday"): (["Hobbiton Toys", "Bywater Market", "Westfarthing Mail Order"], 38000), ("2025-12", "Charity"): (["Bywater Parish Alms"], 20000)}
PAY_START, PAY_EVERY = date(2025, 1, 3), 14
RENT_UP = ("2025-10", 1525)
RAISE = ("2025-07", 1960)
NET_PAY = 1880

MAPPING_NOTES = {"Paycheck": "Rosie, paid every two weeks", "Contract Income": "Sam, garden contracts",
                 "Venmo Pass-Through": "money moved for friends, not part of the household budget"}
EVENTS = [("2025-03", "Sam takes on garden contracts", "job", "Sam"), ("2025-07", "Rosie promoted at Hobbiton Mill", "job", "Rosie"),
          ("2025-10", "Moved to a bigger hole, rent goes up", "move", None), ("2026-02", "Elanor is born", "family", None)]   # (m, label, kind, person)
RULES = {
    "people": ["Rosie", "Sam"],
    "income_by_person": {"Rosie": ["Paycheck"], "Sam": ["Contract Income"]},
    "payer_prefix_by_person": {"Rosie": ["Rosie "], "Sam": ["Sam "]},
    "employers": [{"match": "hobbiton mill", "name": "Hobbiton Mill"}],
    "merchant_rules": [["green dragon", "Green Dragon Inn"], ["bywater market", "Bywater Market"], ["shire power", "Shire Power & Light"]],
    "catchall_items": ["Emergency Fund", "Holiday", "Generosity"],
    "goal_savings_groups": ["Savings"],
}
GOALS = [
    {"key": "gross_annual", "value": 78000, "unit": "usd", "label": "Gross salary, annual", "note": "Invented for the sample household."},
    {"key": "pay_frequency", "value": 26, "unit": "paychecks/yr", "label": "Pay frequency", "note": "26 = every two weeks; two or three months carry three paychecks."},
    {"key": "giving_floor", "value": 0.05, "unit": "share of gross", "label": "Giving floor", "note": "Never below this share of gross."},
    {"key": "giving_target", "value": 0.07, "unit": "share of gross", "label": "Giving target", "note": "Work toward this share of gross."},
    {"key": "joy_target", "value": 0.04, "unit": "share of gross", "label": "Savings target", "note": "Share of gross planned into the Savings group."},
    {"key": "housing_cap", "value": 0.35, "unit": "share of take-home", "label": "Housing cap", "note": "Housing and utilities over net paychecks."},
    {"key": "dti_cap", "value": 0.1, "unit": "share of gross", "label": "Debt-to-income cap", "note": "Debt payments over gross; the household's own line, not a lender's."},
    {"key": "housing_dti_cap", "value": 0.28, "unit": "share of gross", "label": "Housing debt-to-income cap", "note": "Rent, utilities and any mortgage over gross."},
    {"key": "investing_per_paycheck", "value": 150, "unit": "usd", "label": "Investing per paycheck", "note": "Deposited outside the budget app; not in the exports."},
]
# accounts and month-end balances (#143): a backfilled year before the exports start, then every month-end but the last, which is left
# for the check-in to confirm. What a month keeps (its net) lands in checking and the brokerage account, so net worth's "what you did"
# (#146) shows up in the balances. The cart is valued quarterly, so the months between are carried. Their own random stream, so the exports
# above stay byte-for-byte as they were.
ACCOUNTS = [  # name, kind, type, person, debt_item, apr, emergency, in_net_worth
    ("Lembas Student Loan", "debt", "student", "Rosie", "Student Loan", "5.5%", "", "yes"),
    ("Green Hills Savings", "asset", "savings", "", "", "", "yes", "yes"),
    ("Bywater Checking", "asset", "cash", "", "", "", "no", "yes"),
    ("Hobbiton Mill Pension", "asset", "retirement", "Rosie", "", "", "no", "yes"),
    ("Pony and Cart", "asset", "vehicle", "Sam", "", "", "no", "yes"),
    ("Tuckborough Brokerage", "asset", "brokerage", "", "", "", "no", "yes"),
]
BALANCES_FROM = (2024, 7)
HOUSEHOLD = {"name": "Sample Household", "title": "Sample Household Ledger", "currency": "USD", "locale": "en-US"}


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


def write_balances(out: Path, export_months: list[str], kept: dict[str, int]):
    rng = random.Random(SEED * 143)
    with open(out / "accounts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["name", "kind", "type", "person", "debt_item", "apr", "escrow", "emergency", "in_net_worth", "active"])
        for name, kind, typ, person, item, apr, em, nw in ACCOUNTS: w.writerow([name, kind, typ, person, item, apr, "", em, nw, "yes"])
    y, m = BALANCES_FROM; ends = []
    while f"{y}-{m:02d}" < export_months[-1]:
        ends.append((y, m)); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    loan, saved, checking, pension, cart, broker = 2100000, 300000, 240000, 1400000, 900000, 500000   # cents
    rows = []
    for y, m in ends:
        bm = f"{y}-{m:02d}"
        loan = max(0, loan + loan * 55 // 12000 - 27500)     # 5.5% APR, $275 a month: what the suggestion works out, so it reads as confirmed
        saved += 30000 + (38000 if bm == "2025-12" else 0) - (60000 if bm == "2026-03" else 0)   # the monthly envelope; the baby's first spring
        was = checking; checking = 180000 + rng.randint(0, 120000)
        pension = pension * (1000 + rng.randint(-12, 18)) // 1000 + 65000   # 10% of gross in, the market does the rest
        broker = broker * (1000 + rng.randint(-15, 20)) // 1000 + kept.get(bm, 0) - (checking - was)   # the month's net, less what stayed in checking
        rows += [("Lembas Student Loan", bm, loan), ("Green Hills Savings", bm, saved), ("Bywater Checking", bm, checking), ("Hobbiton Mill Pension", bm, pension),
                 ("Tuckborough Brokerage", bm, broker)]
        if m % 3 == 0:
            cart = cart * 97 // 100; rows.append(("Pony and Cart", bm, cart))
    with open(out / "balances.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["account", "month", "amount"])
        for a, bm, c in sorted(rows): w.writerow([a, bm, money(c)])


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    months = list(month_list())
    flow_of, kept = {item: flow for g, item, canon, cat, flow, *_ in ITEMS}, {}
    for y, m in months:
        bm, txns, planned = build_month(rng, y, m)
        kept[bm] = sum(c for d, g, item, typ, merch, c, note in txns if flow_of[item] != "exclude")   # the month's net, in cents
        neutral = (y, m) == months[-1]
        tname, bname = (f"{y}-{m:02d}-transactions.csv", f"{y}-{m:02d}-budget.csv") if neutral else \
                       (f"{HEX}-{m:02d}-{y}-EveryDollar-Transactions.csv", f"{HEX}-{m:02d}-{y}-EveryDollar-BudgetItems.csv")
        with open(out / tname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["Group", "Item", "Type", "Date", "Merchant", "Account", "Amount", "Note"])
            for d, g, item, typ, merch, c, note in txns:
                w.writerow([g, item, typ, f"{y}-{m:02d}-{d:02d}" if neutral else f"{m:02d}/{d:02d}/{y}", merch, ACCOUNT, money(c), note])
        spent = {}
        for d, g, item, typ, merch, c, note in txns: spent[item] = spent.get(item, 0) + c
        with open(out / bname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["Group", "Item", "Planned", "Spent", "Remaining"])
            for g, item, canon, cat, flow, *_ in ITEMS:
                p, s = planned.get(item, 0), spent.get(item, 0)
                w.writerow([g, item, money(p), money(s), money(p - s if flow == "income" else p + s)])
    with open(out / "item_mapping.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["Item", "CanonicalItem", "Category", "Flow", "Note", "DebtType"])
        for g, item, canon, cat, flow, *_ in ITEMS: w.writerow([item, canon, cat, flow, MAPPING_NOTES.get(canon, ""), DEBT_TYPES.get(canon, "")])
    write_balances(out, [f"{y}-{m:02d}" for y, m in months], kept)
    for name, obj in [("goals.json", GOALS), ("events.json", [{"m": m, "label": l, "kind": k, **({"person": p} if p else {})} for m, l, k, p in EVENTS]), ("rules.json", RULES), ("household.json", HOUSEHOLD)]:
        (out / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent)
