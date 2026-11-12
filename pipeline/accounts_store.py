"""Accounts and month-end balances (issue #143): every write to `accounts`, `balances` and `balance_history` goes through here, so the
Settings page, Close a month, the CLI and the seed loader check and store them the same way. DB code for the app and run_pipeline.py,
like mapping_store.py; the scripts beside it read accounts.csv and balances.csv through balances.py.

Balances are facts typed by a person (AGENTS.md: raw). A correction replaces the month's row and keeps the old value in
`balance_history`; nothing here derives a balance, except `suggestions`, which only pre-fills a form."""
from __future__ import annotations
import csv, io, re
from decimal import Decimal, InvalidOperation

from psycopg.rows import dict_row

from . import balances as bal

def norm(x: str) -> str:   # run_pipeline.norm: the app's one rule for tidying a typed name
    return re.sub(r"\s+", " ", x or "").strip()


def _x(conn, sql: str, params=()):
    """Execute on a dict-row cursor, whatever the connection's row factory (the seed loader's is tuples)."""
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KINDS = ("debt", "asset")
YES_NO = ("yes", "no")


def _yn(v) -> str:
    return "yes" if v else "no"


def _bool(raw, default: bool, field: str, err: dict) -> bool:
    s = str(raw if raw is not None else "").strip().lower()
    if s == "":
        return default
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    err[field] = "yes or no"
    return default


def parse_amount(raw, field: str, err: dict, allow_blank: bool = True, negative: bool = False) -> Decimal | None:
    s = str(raw if raw is not None else "").strip().replace(",", "").replace("$", "")
    if s == "":
        if not allow_blank:
            err[field] = "required"
        return None
    try:
        v = Decimal(s)
        if not v.is_finite() or abs(v) >= Decimal("1e12"):
            raise InvalidOperation
    except InvalidOperation:
        err[field] = "must be a number"
        return None
    if v < 0 and not negative:
        err[field] = "cannot be negative"
        return None
    return v.quantize(Decimal("0.01"))


def parse_apr(raw, err: dict) -> Decimal | None:
    """An APR as a fraction: "6.5%" and 0.065 are the same; a bare number above 1 is read as a percentage (6.5 is 6.5%)."""
    s = str(raw if raw is not None else "").strip()
    if s == "":
        return None
    pct = s.endswith("%")
    try:
        v = Decimal(s.rstrip("%").strip())
        if not v.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        err["apr"] = "a rate such as 6.5% or 0.065"
        return None
    if pct or v > 1:
        v = v / 100
    if not 0 <= v <= 1:
        err["apr"] = "between 0% and 100%"
        return None
    return v.quantize(Decimal("0.000001"))


def check_account(r: dict, canonical: set[str] | None = None) -> tuple[dict, dict]:
    """One account from a form, a CSV row or a seed: (clean row, {field: message})."""
    err: dict = {}
    g = lambda k: (r.get(k) if isinstance(r.get(k), str) else ("" if r.get(k) is None else str(r.get(k)))).strip()
    c = {"name": norm(g("name")), "kind": g("kind").lower(), "type": g("type").lower() or None, "person": g("person") or None,
         "debt_item": norm(g("debt_item")) or None}
    if not c["name"]:
        err["name"] = "required"
    if c["kind"] not in KINDS:
        err["kind"] = "debt or asset"
    types = bal.DEBT_TYPES if c["kind"] == "debt" else bal.ASSET_TYPES
    if c["type"] and c["kind"] in KINDS and c["type"] not in types:
        err["type"] = f"for {'a debt' if c['kind'] == 'debt' else 'an asset'}: one of {', '.join(types)}, or empty"
    c["apr"], c["escrow"] = parse_apr(r.get("apr"), err), parse_amount(r.get("escrow"), "escrow", err)
    if c["kind"] == "asset":
        for f in ("debt_item", "apr", "escrow"):
            if c[f] is not None:
                err[f] = "only a debt has this"
    if c["debt_item"] and canonical is not None and c["debt_item"] not in canonical:
        err["debt_item"] = f"{c['debt_item']} is not a canonical item (Settings → Item mapping)"
    c["emergency"] = _bool(r.get("emergency"), c["kind"] == "asset" and c["type"] in bal.EMERGENCY_TYPES, "emergency", err)
    if c["emergency"] and c["kind"] == "debt":
        err["emergency"] = "only an asset counts toward the emergency fund"
    c["in_net_worth"] = _bool(r.get("in_net_worth"), True, "in_net_worth", err)
    c["active"] = _bool(r.get("active"), True, "active", err)
    return c, err


def out(c: dict) -> dict:
    """An account as the Settings table, the JSON answer and the CSV show it."""
    return {"name": c["name"], "kind": c["kind"], "type": c["type"] or "", "person": c["person"] or "", "debt_item": c["debt_item"] or "",
            "apr": "" if c["apr"] is None else f"{(Decimal(c['apr']) * 100).normalize():f}%", "escrow": "" if c["escrow"] is None else f"{Decimal(c['escrow']):.2f}",
            "emergency": _yn(c["emergency"]), "in_net_worth": _yn(c["in_net_worth"]), "active": _yn(c["active"])}


def canonical_items(conn) -> set[str]:
    return {r["name"] for r in _x(conn, "SELECT name FROM canonical_items").fetchall()}


def list_accounts(conn, active_only: bool = False) -> list[dict]:
    where = " WHERE active" if active_only else ""
    return _x(conn, f"SELECT {', '.join(bal.ACCOUNT_COLUMNS)} FROM accounts{where} ORDER BY kind DESC, name").fetchall()


COLS = bal.ACCOUNT_COLUMNS


def save_account(conn, c: dict, original: str = "") -> None:
    """Upsert one checked account; `original` renames it, and its balances and their history follow."""
    original = norm(original)
    if original and original != c["name"]:
        _x(conn, "UPDATE accounts SET name=%s WHERE name=%s", (c["name"], original))   # balances follow (ON UPDATE CASCADE)
        _x(conn, "UPDATE balance_history SET account=%s WHERE account=%s", (c["name"], original))
    vals = [c[k] for k in COLS]
    _x(conn, f"INSERT INTO accounts ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))}) "
                 f"ON CONFLICT (name) DO UPDATE SET {', '.join(f'{k}=EXCLUDED.{k}' for k in COLS[1:])}, updated_at=now()", vals)


def row_save(conn, f: dict) -> tuple[dict, dict]:
    """The Settings row editor: (row as shown, errors). Nothing is written when there are errors."""
    c, err = check_account(f, canonical_items(conn))
    orig = norm(f.get("original") or "")
    if not err and c["name"] != orig and _x(conn, "SELECT 1 FROM accounts WHERE name=%s", (c["name"],)).fetchone():
        err["name"] = "already an account"
    if not err:
        save_account(conn, c, orig)
    return out(c) if not err else {k: (v if not isinstance(v, Decimal) else str(v)) for k, v in c.items()}, err


def delete_account(conn, name: str) -> int:
    """Delete an account that has no balances; one with balances is refused (retire it: Active = no), because they are facts."""
    n = _x(conn, "SELECT count(*) AS n FROM balances WHERE account=%s", (name,)).fetchone()["n"]
    if n:
        raise ValueError(f"{name} has {n} month-end balance{'s' * (n != 1)}, which are kept: retire it instead (set Active to no)")
    return _x(conn, "DELETE FROM accounts WHERE name=%s", (name,)).rowcount


def accounts_csv(conn) -> str:
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(COLS)
    for r in list_accounts(conn):
        o = out(r); w.writerow([o[k] for k in COLS])
    return buf.getvalue()


def _csv_rows(body: str) -> list[tuple[int, dict]]:
    reader = csv.DictReader(io.StringIO(body.lstrip("﻿")))
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    return [(reader.line_num, r) for r in reader]


def _problems(problems: list[str]) -> ValueError:
    return ValueError(f"{len(problems)} problem{'s' * (len(problems) != 1)}: " + "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else ""))


def import_accounts(conn, body: str) -> str:
    """Replace the accounts from a CSV (columns as accounts_csv writes them; only name and kind are required). Every line is checked
    first; an account left out of the CSV is deleted, which is refused when it has balances. All or nothing."""
    rows = _csv_rows(body)
    if rows and not {"name", "kind"}.issubset(rows[0][1].keys()):
        raise ValueError("accounts need a header line with at least name,kind (" + ",".join(COLS) + ")")
    canon, clean, problems, seen = canonical_items(conn), [], [], {}
    for n, r in rows:
        c, err = check_account({k: v for k, v in r.items() if k}, canon)
        if not err and c["name"] in seen:
            err["name"] = f"repeats line {seen[c['name']]}"
        problems += [f"line {n}: {f} {m}" + (f" ({c['name']})" if c["name"] else "") for f, m in err.items()]
        if not err:
            seen[c["name"]] = n; clean.append(c)
    gone = [r["name"] for r in list_accounts(conn) if r["name"] not in seen]
    for name in gone:
        k = _x(conn, "SELECT count(*) AS n FROM balances WHERE account=%s", (name,)).fetchone()["n"]
        if k:
            problems.append(f"{name} is not in the CSV but has {k} balance{'s' * (k != 1)}: keep its line (Active no to retire it)")
    if problems:
        raise _problems(problems)
    for c in clean:
        save_account(conn, c)
    for name in gone:
        _x(conn, "DELETE FROM accounts WHERE name=%s", (name,))
    return f"{len(clean)} account{'s' * (len(clean) != 1)}"


def set_balance(conn, account: str, month: str, amount: Decimal, source: str = "entered", by: str = "") -> bool:
    """Store one month-end balance. A different value replaces the row and keeps the old one in balance_history; the same value
    changes nothing (and a confirmation of a carried value keeps it carried). Returns whether anything changed."""
    old = _x(conn, "SELECT amount, source, entered_at, entered_by FROM balances WHERE account=%s AND budget_month=%s", (account, month)).fetchone()
    if old and Decimal(old["amount"]) == amount:
        return False
    if old:
        _x(conn, "INSERT INTO balance_history (account, budget_month, amount, source, entered_at, entered_by, event, event_by) "
                     "VALUES (%s,%s,%s,%s,%s,%s,'replaced',%s)", (account, month, old["amount"], old["source"], old["entered_at"], old["entered_by"], by))
    _x(conn, "INSERT INTO balances (account, budget_month, amount, source, entered_at, entered_by) VALUES (%s,%s,%s,%s,now(),%s) "
                 "ON CONFLICT (account, budget_month) DO UPDATE SET amount=EXCLUDED.amount, source=EXCLUDED.source, entered_at=now(), entered_by=EXCLUDED.entered_by",
                 (account, month, amount, source, by))
    return True


def delete_balance(conn, account: str, month: str, by: str = "") -> bool:
    old = _x(conn, "DELETE FROM balances WHERE account=%s AND budget_month=%s RETURNING amount, source, entered_at, entered_by", (account, month)).fetchone()
    if old:
        _x(conn, "INSERT INTO balance_history (account, budget_month, amount, source, entered_at, entered_by, event, event_by) "
                     "VALUES (%s,%s,%s,%s,%s,%s,'deleted',%s)", (account, month, old["amount"], old["source"], old["entered_at"], old["entered_by"], by))
    return bool(old)


def import_balances(conn, body: str, by: str = "") -> str:
    """Backfill or correct month-end balances from CSV: account,month,amount (a header line is optional; a source column is read when
    present, else `entered`). Every line is checked before anything is stored; all or nothing."""
    text = body.lstrip("﻿")
    first = text.splitlines()[0].lower() if text.strip() else ""
    if first and "account" not in first:
        text = "account,month,amount\n" + text
    rows = _csv_rows(text)
    known = {r["name"] for r in list_accounts(conn)}
    clean, problems, seen = [], [], {}
    for n, r in rows:
        err: dict = {}
        acct, m = norm(r.get("account") or ""), (r.get("month") or "").strip()
        if acct not in known:
            err["account"] = f"{acct or '(blank)'} is not an account (Settings → Accounts)"
        if not MONTH_RE.match(m):
            err["month"] = "must look like YYYY-MM"
        amt = parse_amount(r.get("amount"), "amount", err, allow_blank=False, negative=True)
        src = (r.get("source") or "entered").strip().lower() or "entered"
        if src not in ("entered", "carried"):
            err["source"] = "entered or carried"
        if not err and (acct, m) in seen:
            err["month"] = f"{acct} {m} repeats line {seen[(acct, m)]}"
        problems += [f"line {n}: {f} {msg}" for f, msg in err.items()]
        if not err:
            seen[(acct, m)] = n; clean.append((acct, m, amt, src))
    if not clean and not problems:
        problems.append("no balances in the CSV")
    if problems:
        raise _problems(problems)
    changed = sum(set_balance(conn, a, m, v, s, by) for a, m, v, s in clean)
    return f"{len(clean)} balance{'s' * (len(clean) != 1)} ({changed} new or changed)"


def balances_csv(conn) -> str:
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(bal.BALANCE_COLUMNS)
    for r in _x(conn, "SELECT account, budget_month, amount, source FROM balances ORDER BY account, budget_month").fetchall():
        w.writerow([r["account"], r["budget_month"], f"{r['amount']:.2f}", r["source"]])
    return buf.getvalue()


def payments(conn, month: str) -> dict[str, float]:
    """Dollars paid to each canonical item with Flow debt in a budget month, from the built ledger (as the Debts tab counts them)."""
    return {r["canonical_item"]: float(r["paid"]) for r in _x(conn, 
        "SELECT canonical_item, -sum(amount) AS paid FROM ledger WHERE budget_month=%s AND flow='debt' AND in_totals GROUP BY canonical_item", (month,)).fetchall()}


def suggestions(conn, month: str) -> list[dict]:
    """The form for one month-end: each active account (and any retired one with a balance that month) with its stored balance, or
    the suggestion from the month before (`pipeline/balances.suggest`). `status`: entered, carried (both confirmed) or suggested."""
    stored = {r["account"]: r for r in _x(conn, "SELECT account, amount, source, entered_at FROM balances WHERE budget_month=%s", (month,)).fetchall()}
    prev_m = bal.prev_month(month)
    prev = {r["account"]: r for r in _x(conn, 
        "SELECT DISTINCT ON (account) account, budget_month, amount FROM balances WHERE budget_month < %s ORDER BY account, budget_month DESC", (month,)).fetchall()}
    paid_by: dict[str, dict[str, float]] = {}
    def paid(m: str, item: str | None) -> float:
        if not item:
            return 0.0
        if m not in paid_by:
            paid_by[m] = payments(conn, m)
        return paid_by[m].get(item, 0.0)
    out_rows = []
    for a in list_accounts(conn):
        if not a["active"] and a["name"] not in stored:
            continue
        p = prev.get(a["name"])
        sug = None
        if p is not None:   # chained through any months since the last balance, as the build estimates them
            amount = float(p["amount"])
            for m in bal.months(bal.next_month(p["budget_month"]), month):
                amount = bal.suggest(a["kind"], amount, paid(m, a["debt_item"]), float(a["apr"]) if a["apr"] is not None else None,
                                     float(a["escrow"]) if a["escrow"] is not None else None)
            sug = round(amount, 2)
        s = stored.get(a["name"])
        out_rows.append({**a, "stored": float(s["amount"]) if s else None, "status": s["source"] if s else "suggested", "suggestion": sug,
                         "previous": float(p["amount"]) if p else None, "previous_month": p["budget_month"] if p else None,
                         "payment": paid(month, a["debt_item"]) if a["debt_item"] else None, "value": float(s["amount"]) if s else sug,
                         "stale": bool(p) and p["budget_month"] != prev_m and not s})
    return out_rows


def save_month(conn, month: str, values: dict[str, str], by: str = "") -> tuple[int, dict]:
    """The Close a month form: one typed value per account. A value equal to the suggestion, where there was no balance, is stored
    as `carried` (accepting it counts as confirming); any other value as `entered`. A blank leaves the account unconfirmed.
    Returns (balances changed, {account: problem})."""
    if not MONTH_RE.match(month):
        raise ValueError("month must look like YYYY-MM")
    form = {r["name"]: r for r in suggestions(conn, month)}
    errors, changed, todo = {}, 0, []
    for name, raw in values.items():
        if name not in form:
            errors[name] = "not an account"; continue
        err: dict = {}
        v = parse_amount(raw, "amount", err, negative=True)
        if err:
            errors[name] = err["amount"]; continue
        if v is not None:
            todo.append((name, v))
    if errors:
        return 0, errors
    for name, v in todo:
        f = form[name]
        src = "carried" if f["stored"] is None and f["suggestion"] is not None and Decimal(str(f["suggestion"])).quantize(Decimal("0.01")) == v else "entered"
        if f["stored"] is not None and Decimal(str(f["stored"])).quantize(Decimal("0.01")) == v:
            continue
        changed += set_balance(conn, name, month, v, src, by)
    return changed, {}


def month_status(conn, month: str) -> dict:
    """How far the month's balances are: {"accounts", "confirmed", "to_do": [names]} over the accounts the form lists."""
    rows = suggestions(conn, month)
    todo = [r["name"] for r in rows if r["stored"] is None]
    return {"accounts": len(rows), "confirmed": len(rows) - len(todo), "to_do": todo}


def seed(conn, accounts_path, balances_path) -> None:
    """First boot (seed_reference_tables): accounts.csv into an empty accounts table, then balances.csv into an empty balances table.
    A bad line stops seeding with the message naming it."""
    if accounts_path.exists() and _x(conn, "SELECT count(*) AS n FROM accounts").fetchone()["n"] == 0:
        try:
            import_accounts(conn, accounts_path.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            raise ValueError(f"seed {accounts_path}: {e}") from None
    if balances_path.exists() and _x(conn, "SELECT count(*) AS n FROM balances").fetchone()["n"] == 0 and \
            _x(conn, "SELECT count(*) AS n FROM accounts").fetchone()["n"]:
        try:
            import_balances(conn, balances_path.read_text(encoding="utf-8-sig"), "seed")
        except ValueError as e:
            raise ValueError(f"seed {balances_path}: {e}") from None
