#!/usr/bin/env python3 -I
"""Build the canonical ledger from staged EveryDollar exports + item_mapping.csv + cpi_u.csv."""
import csv, os, re, json, collections
from decimal import Decimal as D

def norm(x): return re.sub(r"\s+"," ",(x or "").replace(" "," ")).strip()
rx = re.compile(r"^[0-9a-f]+-(\d\d)-(\d{4})-EveryDollar-Transactions\.csv$")

# ---- reference tables ----
mapping = {norm(r["Item"]): r for r in csv.DictReader(open("item_mapping.csv", newline=""))}
cpi = {r["month"]: float(r["cpi"]) for r in csv.DictReader(open("cpi_u.csv"))}
# ---- rules (configurable; rules.json in the working dir overrides these defaults) ----
RULES = {
  "cpi_base_month": None,              # default: latest month in cpi_u.csv
  "people": [],                        # earners/payers, e.g. ["A", "B"]
  "income_by_person": {},              # person -> list of CanonicalItem names that are their income
  "payer_prefix_by_person": {},        # person -> item-name prefixes that mark their personal spending
  "payer_items_by_person": {},         # person -> explicit item names
  "employers": [],                     # [{"match": regex on merchant, "name": "Employer"}]
  "employer_before": None,             # {"month": "YYYY-MM", "name": "..."}: paychecks before this month with no merchant
  "debt_plug_until": None,             # "YYYY-MM": debt "Interest" rows up to here are balancing plugs, not payments
  "transfer_item_words": None,         # item-name words that mark a transfer ("Transfer to <word>"; regex), or None for the defaults
  "transfer_merchants": None,          # regex of whole merchant names that are envelope moves (e.g. "office redistribution"), tried with the defaults
  "item_name_suffixes": None,          # regexes for the suffixes an envelope's name gains between years (" 2", " Fund", " Goal"); None = those. Settings hints and the mapping draft only
  "transfer_item_names": None,         # regex of whole item names that are fund moves ("Fund Transfer"), drafted as Flow transfer; None = the built-in list. Mapping draft only
  "verify_transfer_prefixes": [],      # CanonicalItem prefixes whose "verify" rows are moves between envelopes, not spending
  "catchall_items": [],                # CanonicalItem names that act as catch-alls (the Where it went tab charts them)
  "paycheck_min": 500,                 # a paycheck row (paycheck_items) above this many dollars counts as one deposit; smaller ones are deductions
  "goal_giving_categories": ["Giving"],              # categories whose spend is giving, for the Goals tab
  "goal_housing_categories": ["Housing", "Utilities"],  # categories counted against the housing cap
  "goal_savings_groups": [],           # budget groups whose planned amounts count toward the savings target
  # ---- roles: names the pipeline and the page act on (issue #162); the only place these defaults live outside the schema ----
  "paycheck_items": ["Paycheck"],      # CanonicalItems that are paychecks: employers, deposits (paycheck_min), the Employer chart, the review's count
  "recurring_categories": ["Subscriptions", "Utilities", "Insurance"],   # categories whose expense rows the Recurring tab lists
  "plan_keep_categories": ["Housing", "Debt", "Insurance"],              # categories whose suggested plan stays the current plan (Last month tab)
  "income_groups": ["Income"],         # budget groups that hold income: no carry-forward-into-a-fund there; source profiles and the draft default to them
  "debt_groups": ["Debt"],             # budget groups that hold debt payments: the debt-plug rule and the off-budget outflow count
  "loan_categories": ["Family Loan"],  # categories of loans between family: their inflows and repayments are counted on the Method tab
  "offbudget_since": None,             # "YYYY-MM": count unpaired off-budget outflows from here (exclusions table)
  "merchant_rules": [                  # [regex, display name]; household extras go in rules.json and are tried first
    [r"^(amzn|amazon)", "Amazon"], [r"^wal-?mart|^wm supercenter", "Walmart"], [r"^sams ?club", "Sam's Club"], [r"^costco", "Costco"],
    [r"^target", "Target"], [r"home depot", "Home Depot"], [r"walgreens", "Walgreens"], [r"^cvs", "CVS"], [r"starbucks", "Starbucks"],
    [r"chipotle", "Chipotle"], [r"chick.?fil", "Chick-fil-A"], [r"taco bell", "Taco Bell"], [r"wholefds|whole foods", "Whole Foods"],
    [r"spotify", "Spotify"], [r"netflix", "Netflix"], [r"hulu", "Hulu"], [r"microsoft|xbox", "Microsoft"], [r"venmo", "Venmo"],
    [r"zelle|quickpay", "Zelle"], [r"^shell", "Shell"], [r"chevron", "Chevron"], [r"irs|us treas", "IRS"], [r"turbotax|intuit", "TurboTax"],
  ],
}
if os.path.exists("rules.json"):
    _r = json.load(open("rules.json"))
    if _r.get("merchant_rules"): _r["merchant_rules"] = list(_r["merchant_rules"]) + RULES["merchant_rules"]
    RULES.update({k: v for k, v in _r.items() if v is not None})
CPI_BASE_MONTH = RULES["cpi_base_month"] or max(cpi); CPI_BASE = cpi[CPI_BASE_MONTH]
PAYCHECK_ITEMS, INCOME_GROUPS, DEBT_GROUPS = set(RULES["paycheck_items"]), set(RULES["income_groups"]), set(RULES["debt_groups"])

def employer(bm, merchant):
    m = merchant.lower()
    for e in RULES["employers"]:
        if re.search(e["match"], m): return e["name"]
    eb = RULES.get("employer_before")
    if eb and bm <= eb["month"]: return eb["name"]
    return ""

ITEM_WORDS = RULES.get("transfer_item_words") or r"savings|checking|emergency(?: fund)?|sinking fund|slush|fund|budget"
TRANSFER_RX = re.compile(
    r"^(transfer|rebalance|fund rebalance|fund transfer|redistribution|"
    + (r"(?:" + RULES["transfer_merchants"] + r")|" if RULES.get("transfer_merchants") else "")
    + r"transfer(?:ring)? (?:to|from) (?:" + ITEM_WORDS + r")\b.*)$", re.I)

def is_transfer_merchant(m):
    m = norm(m).lower()
    return bool(TRANSFER_RX.match(m))

# US state, DC and territory codes, less the ones that are also words at the end of a business name (Co, In, Or, Me, Hi, Ok,
# De, La, Pa, Ma, Id, Oh): stripping "Pizza Co" to "Pizza" is worse than leaving "Denver Co" unstripped. merchant_rules can tidy those.
STATES = ("al|ak|az|ar|ca|ct|dc|fl|ga|il|ia|ks|ky|md|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|"
          "ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|pr|gu|vi")

def merchant_norm(m):
    s = norm(m)
    s = re.sub(r"^(paypal|pp\*?|tst\*?|sq\*?|sp\s{1,2})\s*", "", s, flags=re.I)
    s = re.sub(r"\b(direct dep|dir dep|payroll|web pmts|autopay|debit card purchase|purchase)\b.*$", "", s, flags=re.I)
    s = re.sub(r"[#*].*$", "", s)
    s = re.sub(r"\b[a-z]*\d+[a-z0-9]*\b", "", s, flags=re.I)         # store codes / confirmation numbers
    s = re.sub(r"\b(" + STATES + r")\b$", "", s.strip(), flags=re.I)  # trailing state or territory code
    s = re.sub(r"\s+", " ", s).strip(" -.,")
    for pat, name in RULES["merchant_rules"]:
        if re.search(pat, s, flags=re.I): return name
    return s.title() if s else ""

# ---- load ----
rows = []
for fn in sorted(os.listdir("txns")):
    m = rx.match(fn)
    if not m: continue
    bm = f"{m.group(2)}-{m.group(1)}"
    with open(os.path.join("txns", fn), newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            rows.append({"BudgetMonth": bm, "SourceFile": fn, "SourceRow": i+2,
                         "Group": norm(r["Group"]), "Item": norm(r["Item"]), "Type": r["Type"],
                         "Date": r["Date"], "Merchant": norm(r["Merchant"]), "Amount": D(r["Amount"]), "Note": r.get("Note","")})

excluded = collections.Counter(); excluded_amt = collections.defaultdict(float)
out = []
for r in rows:
    mp = mapping.get(r["Item"])
    if mp is None: raise SystemExit(f"unmapped item: {r['Item']!r}")
    flow = mp["Flow"]
    if flow == "exclude": excluded[r["Item"]] += 1; excluded_amt[r["Item"]] += abs(float(r["Amount"])); continue
    mm, dd, yy = r["Date"].split("/")
    cal = f"{yy}-{mm}"
    amt = float(r["Amount"])
    is_plug = (flow == "plug") or (r["Group"] in DEBT_GROUPS and flow == "debt" and amt > 0 and r["BudgetMonth"] <= (RULES["debt_plug_until"] or "0000-00"))
    # merchant == item name only counts as a transfer when it is an inflow (carry-forward/rebalance into a fund);
    # 2018 hand-typed outflows ("Rent"/"Rent", "Spotify"/"Spotify") are real bills.
    is_transfer = (flow == "transfer") or is_transfer_merchant(r["Merchant"]) or \
                  (norm(r["Merchant"]).lower() == r["Item"].lower() and amt > 0 and r["Group"] not in INCOME_GROUPS)
    ci = mp["CanonicalItem"]
    if flow == "verify" and RULES["verify_transfer_prefixes"] and ci.startswith(tuple(RULES["verify_transfer_prefixes"])):
        flow, is_transfer = "transfer", True; ci = "Transfer"
    earner = ""
    if flow == "income":
        earner = next((p for p, items in RULES["income_by_person"].items() if ci in items), "Household")
    payer = ""
    if flow in ("expense","debt"):
        payer = next((p for p, pre in RULES["payer_prefix_by_person"].items() if ci.startswith(tuple(pre))), None) \
             or next((p for p, items in RULES["payer_items_by_person"].items() if ci in items), None) or "Household"
    emp = employer(r["BudgetMonth"], r["Merchant"]) if ci in PAYCHECK_ITEMS else ""
    c = cpi.get(cal) or cpi.get(r["BudgetMonth"]) or CPI_BASE
    out.append({
        "BudgetMonth": r["BudgetMonth"], "CalendarMonth": cal, "Date": f"{yy}-{mm}-{dd}",
        "Group": r["Group"], "Item": r["Item"], "Type": r["Type"],
        "CanonicalItem": ci, "Category": mp["Category"], "Flow": flow,
        "Merchant": r["Merchant"], "MerchantNorm": merchant_norm(r["Merchant"]),
        "Amount": f"{amt:.2f}", "AmountReal": f"{amt * CPI_BASE / c:.2f}",
        "Earner": earner, "Payer": payer, "Employer": emp,
        "IsTransfer": int(is_transfer), "IsPlug": int(is_plug), "InTotals": int(not (is_transfer or is_plug or flow in ("verify",))),
        "HasNote": int(bool(r["Note"].strip())), "Note": r["Note"].replace("\n"," / "),
        "SourceFile": r["SourceFile"], "SourceRow": r["SourceRow"],
    })

# ---- pair transfers (same budget month, |amount| equal, opposite sign, different item, both flagged or one flagged) ----
by = collections.defaultdict(list)
for i, o in enumerate(out):
    if o["IsTransfer"]: by[(o["BudgetMonth"], abs(float(o["Amount"])))].append(i)
pair_id = 0
for k, idxs in by.items():
    pos = [i for i in idxs if float(out[i]["Amount"]) > 0]; neg = [i for i in idxs if float(out[i]["Amount"]) < 0]
    for a, b in zip(pos, neg):
        pair_id += 1; out[a]["TransferPair"] = out[b]["TransferPair"] = pair_id
for o in out: o.setdefault("TransferPair", "")

cols = ["BudgetMonth","CalendarMonth","Date","Group","Item","Type","CanonicalItem","Category","Flow","Merchant","MerchantNorm",
        "Amount","AmountReal","Earner","Payer","Employer","IsTransfer","IsPlug","TransferPair","InTotals","HasNote","Note","SourceFile","SourceRow"]
with open("ledger.csv","w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(out)

json.dump(dict(excluded), open("excluded.json", "w"))
json.dump({k: round(v, 2) for k, v in excluded_amt.items()}, open("excluded_amounts.json", "w"))   # gross dollars moved per excluded item (pass-through money nets to about zero)
print(f"rows in: {len(rows)}  excluded: {dict(excluded)}  rows out: {len(out)}")
print(f"transfers: {sum(o['IsTransfer'] for o in out)}  paired: {sum(1 for o in out if o['TransferPair'])}  plugs: {sum(o['IsPlug'] for o in out)}")
print(f"in totals: {sum(o['InTotals'] for o in out)}  CPI base {CPI_BASE_MONTH}={CPI_BASE}")
flows = collections.Counter(o["Flow"] for o in out); print("flows:", dict(flows))
