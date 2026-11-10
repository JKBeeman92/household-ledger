"""Source profiles: read a budget tool's own two CSVs (transactions, budget) through a column mapping (issue #96).

The pipeline's internal schema is EveryDollar's export format, which stays the built-in profile:
  transactions  Group, Item, Type, Date (MM/DD/YYYY), Merchant, Account, Amount (income +, spending -), Note
  budget items  Group, Item, Planned (>= 0), Spent (income +, spending -), Remaining
A profile turns another tool's files into rows of that shape, so everything downstream (the database tables, the four pipeline
scripts, the goldens) is unchanged. The original bytes are kept as uploaded; a corrected profile is re-applied to them.

A profile is plain JSON, so it can be exported and imported:
  {"name": "Bree Bank",
   "transactions": {"headers": [...], "columns": {"date": "Posted", "amount": null, "debit": "Debit", "credit": "Credit",
                    "group": "Category", "item": "Subcategory", "merchant": "Description", "account": null, "note": "Memo",
                    "type": null},
                    "date_format": "YYYY-MM-DD", "decimal": ".", "thousands": ",",
                    "amount_mode": "signed" | "split" | "type", "expense_sign": "negative" | "positive",
                    "income_values": ["income"], "income_groups": ["Income"]},
   "budget": {"headers": [...], "columns": {"group": ..., "item": ..., "planned": ..., "spent": ...},
              "decimal": ".", "thousands": ",", "spent_sign": "negative" | "positive", "income_groups": ["Income"]}}

Standalone: `python -m pipeline.sources normalize --profile p.json --month 2026-03 --transactions t.csv --budget b.csv --out DIR`
writes the pair as EveryDollar-named files, which the pipeline scripts then read like any other export.
"""
from __future__ import annotations
import argparse, csv, hashlib, io, json, re, sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

KINDS = ("transactions", "budget_items")
TXN_FIELDS = ("date", "amount", "debit", "credit", "type", "merchant", "account", "note", "group", "item")
BUDGET_FIELDS = ("group", "item", "planned", "spent")
REQUIRED = {"transactions": ("date", "group", "item"), "budget_items": ("group", "item", "planned", "spent")}
DATE_FORMATS = ("MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD", "DD.MM.YYYY", "YYYY/MM/DD", "DD-MM-YYYY", "MM-DD-YYYY")
ED_TXN = ["Group", "Item", "Type", "Date", "Merchant", "Account", "Amount", "Note"]
ED_BUDGET = ["Group", "Item", "Planned", "Spent", "Remaining"]

# header names that propose each field, best first; matching ignores case, spaces, underscores and punctuation
SYNONYMS = {
    "date": ["date", "transaction date", "posted", "posting date", "posted date", "booking date", "value date", "day"],
    "amount": ["amount", "amt", "value", "sum", "transaction amount"],
    "debit": ["debit", "withdrawal", "withdrawals", "outflow", "money out", "paid out", "spent"],
    "credit": ["credit", "deposit", "deposits", "inflow", "money in", "paid in", "received"],
    "type": ["type", "transaction type", "kind", "direction"],
    "merchant": ["merchant", "payee", "description", "name", "counterparty", "vendor", "details"],
    "account": ["account", "account name", "card"],
    "note": ["note", "notes", "memo", "comment", "comments", "reference"],
    "group": ["group", "category group", "master category", "budget group", "category"],
    "item": ["item", "subcategory", "sub category", "envelope", "budget item", "category", "line"],
    "planned": ["planned", "budgeted", "budget", "assigned", "allocated", "plan"],
}
BUDGET_SYNONYMS = dict(SYNONYMS, spent=["spent", "actual", "activity", "actual spent", "outflows", "used"])


class ProfileError(ValueError):
    pass


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


# ---------------------------------------------------------------- reading files
def read_csv(data: bytes) -> tuple[list[str], list[dict]]:
    """Header and rows of a CSV in UTF-8 (with or without BOM) or Windows-1252; the delimiter is sniffed (comma, semicolon, tab)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rd = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in rd if any(c.strip() for c in r)]
    if not rows:
        raise ProfileError("the file is empty")
    header = [h.strip() for h in rows[0]]
    if len(set(header)) != len(header):
        raise ProfileError("the header row repeats a column name")
    return header, [dict(zip(header, (c.strip() for c in r))) for r in rows[1:]]


def headers_of(data: bytes) -> list[str]:
    return read_csv(data)[0]


# ---------------------------------------------------------------- proposing a mapping
def guess_columns(headers: list[str], kind: str) -> dict:
    """Propose a column for each field from the header names; each header is used at most once. None where nothing fits."""
    syn = SYNONYMS if kind == "transactions" else BUDGET_SYNONYMS
    fields = TXN_FIELDS if kind == "transactions" else BUDGET_FIELDS
    by_key = {_key(h): h for h in headers}
    taken: set[str] = set()
    out: dict[str, str | None] = {}
    for f in fields:
        out[f] = None
        for name in syn[f]:
            h = by_key.get(name)
            if h and h not in taken:
                out[f] = h; taken.add(h); break
    if kind == "transactions" and out["amount"]:   # one signed amount column wins over a stray "spent"/"deposit" guess
        for f in ("debit", "credit"):
            if out[f]: taken.discard(out[f]); out[f] = None
    return out


def _guess_date_format(values: list[str]) -> str:
    vals = [v for v in values if v][:200]
    if vals and all(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", v) for v in vals): return "YYYY-MM-DD"
    if vals and all(re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", v) for v in vals): return "YYYY/MM/DD"
    if vals and all(re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", v) for v in vals): return "DD.MM.YYYY"
    for sep in "/-":
        pat = rf"(\d{{1,2}}){re.escape(sep)}(\d{{1,2}}){re.escape(sep)}\d{{4}}"
        if vals and all(re.fullmatch(pat, v) for v in vals):
            firsts = [int(re.fullmatch(pat, v).group(1)) for v in vals]
            seconds = [int(re.fullmatch(pat, v).group(2)) for v in vals]
            day_first = max(firsts) > 12 and max(seconds) <= 12
            return ("DD" + sep + "MM" + sep + "YYYY") if day_first else ("MM" + sep + "DD" + sep + "YYYY")
    return "MM/DD/YYYY"


def _guess_separators(values: list[str]) -> tuple[str, str]:
    vals = [v for v in values if v]
    if any(re.search(r"\d,\d{2}$", v) for v in vals) and not any(re.search(r"\d\.\d{2}$", v) for v in vals):
        return ",", "."
    return ".", ","


def propose(kind: str, data: bytes) -> dict:
    """A starting profile section for one file, from its headers and values; the user confirms or corrects it."""
    header, rows = read_csv(data)
    cols = guess_columns(header, kind)
    col = lambda f: [r.get(cols[f], "") for r in rows] if cols.get(f) else []
    if kind == "transactions":
        money = col("amount") or col("debit") + col("credit")
        dec, th = _guess_separators(money)
        mode = "signed" if cols["amount"] else "split"
        amounts = [parse_money(v, dec, th) for v in col("amount") if v]
        if mode == "signed" and amounts and all(a >= 0 for a in amounts) and cols["type"]:
            mode = "type"
        return {"headers": header, "columns": cols, "date_format": _guess_date_format(col("date")), "decimal": dec, "thousands": th,
                "amount_mode": mode, "expense_sign": "negative" if any(a < 0 for a in amounts) or mode != "signed" else "positive",
                "income_values": ["income", "credit", "deposit"], "income_groups": ["Income"]}
    dec, th = _guess_separators(col("planned") + col("spent"))
    # how spending is signed: by the majority of the non-income rows (one refund or balancing entry must not decide it)
    signs = [parse_money(r.get(cols["spent"], ""), dec, th) for r in rows if cols.get("spent") and cols.get("group")
             and r.get(cols["group"], "").strip().lower() != "income" and r.get(cols["spent"], "").strip()]
    neg, pos = sum(1 for v in signs if v < 0), sum(1 for v in signs if v > 0)
    return {"headers": header, "columns": cols, "decimal": dec, "thousands": th,
            "spent_sign": "negative" if neg > pos else "positive", "income_groups": ["Income"]}


def which_kind(header: list[str], profile: dict) -> str | None:
    """The kind of file this header belongs to under the profile (the same set of column names), or None."""
    hs = set(header)
    for kind, sec in (("transactions", profile.get("transactions")), ("budget_items", profile.get("budget"))):
        if sec and set(sec.get("headers") or []) == hs:
            return kind
    return None


def match_profile(header: list[str], profiles: list[dict]) -> tuple[dict, str] | None:
    """The first saved profile that recognises this header, with the kind it recognises it as."""
    for p in profiles:
        k = which_kind(header, p)
        if k:
            return p, k
    return None


# ---------------------------------------------------------------- applying a profile
def parse_money(v: str, decimal: str = ".", thousands: str = ",") -> Decimal:
    s = (v or "").strip().replace(" ", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = re.sub(r"[^\d\-+" + re.escape(decimal) + re.escape(thousands) + "]", "", s)   # currency symbols, letters
    if thousands:
        s = s.replace(thousands, "")
    if decimal != ".":
        s = s.replace(decimal, ".")
    if s in ("", "-", "+"):
        return Decimal(0)
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ProfileError(f"{v!r} is not an amount")
    return -d if neg else d


def parse_date(v: str, fmt: str) -> str:
    """The date in EveryDollar's MM/DD/YYYY."""
    if fmt not in DATE_FORMATS:
        raise ProfileError(f"unknown date format {fmt!r}; use one of {', '.join(DATE_FORMATS)}")
    py = fmt.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d")
    try:
        return datetime.strptime((v or "").strip(), py).strftime("%m/%d/%Y")
    except ValueError:
        raise ProfileError(f"{v!r} is not a date in {fmt}")


def _money(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01')):.2f}"


def validate_section(kind: str, sec: dict | None) -> None:
    if not isinstance(sec, dict):
        raise ProfileError(f"the profile has no {'transactions' if kind == 'transactions' else 'budget'} section")
    cols = sec.get("columns") or {}
    missing = [f for f in REQUIRED[kind] if not cols.get(f)]
    if missing:
        raise ProfileError(f"{'transactions' if kind == 'transactions' else 'budget'}: choose a column for {', '.join(missing)}")
    if kind == "transactions":
        mode = sec.get("amount_mode", "signed")
        if mode not in ("signed", "split", "type"):
            raise ProfileError("transactions: amount_mode must be signed, split or type")
        if mode in ("signed", "type") and not cols.get("amount"):
            raise ProfileError("transactions: choose the amount column")
        if mode == "split" and not (cols.get("debit") or cols.get("credit")):
            raise ProfileError("transactions: choose the debit and/or credit columns")
        if mode == "type" and not cols.get("type"):
            raise ProfileError("transactions: choose the type column, which says whether a row is income")
        if sec.get("date_format", "MM/DD/YYYY") not in DATE_FORMATS:
            raise ProfileError(f"transactions: unknown date format; use one of {', '.join(DATE_FORMATS)}")


def validate_profile(p: dict) -> dict:
    if not isinstance(p, dict) or not str(p.get("name", "")).strip():
        raise ProfileError("a profile needs a name")
    validate_section("transactions", p.get("transactions"))
    validate_section("budget_items", p.get("budget"))
    return p


def apply(kind: str, data: bytes, profile: dict) -> list[dict]:
    """The file's rows in the internal (EveryDollar) shape. Raises ProfileError naming the row and column that could not be read."""
    sec = profile.get("transactions") if kind == "transactions" else profile.get("budget")
    validate_section(kind, sec)
    header, rows = read_csv(data)
    cols = sec["columns"]
    absent = [c for c in cols.values() if c and c not in header]
    if absent:
        raise ProfileError(f"columns not in the file: {', '.join(absent)}")
    dec, th = sec.get("decimal", "."), sec.get("thousands", ",")
    get = lambda r, f: (r.get(cols[f], "") if cols.get(f) else "").strip()
    out = []
    for n, r in enumerate(rows, start=2):
        try:
            if kind == "transactions":
                mode = sec.get("amount_mode", "signed")
                if mode == "split":
                    amt = parse_money(get(r, "credit"), dec, th).copy_abs() - parse_money(get(r, "debit"), dec, th).copy_abs()
                elif mode == "type":
                    a = parse_money(get(r, "amount"), dec, th).copy_abs()
                    amt = a if get(r, "type").lower() in {v.lower() for v in sec.get("income_values", [])} else -a
                else:
                    a = parse_money(get(r, "amount"), dec, th)
                    amt = a if sec.get("expense_sign", "negative") == "negative" else -a
                if not get(r, "date") and amt == 0:
                    continue   # a blank or summary line
                # Type is EveryDollar's: the kind of budget line (income or expense), not the sign of this row
                income = (mode == "type" and get(r, "type").lower() in {v.lower() for v in sec.get("income_values", [])}) or \
                         get(r, "group").lower() in {g.lower() for g in sec.get("income_groups", ["Income"])}
                out.append({"Group": get(r, "group"), "Item": get(r, "item"), "Type": "Income" if income else "Expense",
                            "Date": parse_date(get(r, "date"), sec.get("date_format", "MM/DD/YYYY")), "Merchant": get(r, "merchant"),
                            "Account": get(r, "account"), "Amount": _money(amt), "Note": get(r, "note")})
            else:
                if not get(r, "item"):
                    continue
                income = get(r, "group").lower() in {g.lower() for g in sec.get("income_groups", ["Income"])}
                planned = parse_money(get(r, "planned"), dec, th).copy_abs()
                spent = parse_money(get(r, "spent"), dec, th)
                if not income and sec.get("spent_sign", "negative") == "positive":
                    spent = -spent
                remaining = planned - spent if income else planned + spent
                out.append({"Group": get(r, "group"), "Item": get(r, "item"), "Planned": _money(planned), "Spent": _money(spent), "Remaining": _money(remaining)})
        except ProfileError as e:
            raise ProfileError(f"row {n}: {e}") from None
    if not out:
        raise ProfileError("no rows could be read")
    return out


def to_everydollar(kind: str, rows: list[dict], month: str, seed: bytes = b"") -> tuple[str, bytes]:
    """The rows as an EveryDollar-named CSV for the standalone scripts: (filename, bytes)."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ProfileError("the budget month must be YYYY-MM")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=ED_TXN if kind == "transactions" else ED_BUDGET, lineterminator="\n")
    w.writeheader(); w.writerows(rows)
    data = buf.getvalue().encode()
    hx = hashlib.sha256(seed or data).hexdigest()[:8]
    yyyy, mm = month.split("-")
    return f"{hx}-{mm}-{yyyy}-EveryDollar-{'Transactions' if kind == 'transactions' else 'BudgetItems'}.csv", data


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.sources", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("propose", help="print a starting profile for a transactions file and a budget file")
    pr.add_argument("--name", required=True); pr.add_argument("--transactions", required=True, type=Path); pr.add_argument("--budget", required=True, type=Path)
    nm = sub.add_parser("normalize", help="write a pair as EveryDollar-named files the pipeline scripts read")
    nm.add_argument("--profile", required=True, type=Path); nm.add_argument("--month", required=True)
    nm.add_argument("--transactions", required=True, type=Path); nm.add_argument("--budget", required=True, type=Path); nm.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "propose":
            print(json.dumps({"name": a.name, "transactions": propose("transactions", a.transactions.read_bytes()),
                              "budget": propose("budget_items", a.budget.read_bytes())}, indent=1))
            return 0
        p = validate_profile(json.loads(a.profile.read_text()))
        a.out.mkdir(parents=True, exist_ok=True)
        for kind, path in (("transactions", a.transactions), ("budget_items", a.budget)):
            raw = path.read_bytes()
            name, data = to_everydollar(kind, apply(kind, raw, p), a.month, seed=raw)
            (a.out / name).write_bytes(data)
            print(a.out / name)
        return 0
    except ProfileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
