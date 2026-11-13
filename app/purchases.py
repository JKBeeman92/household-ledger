"""Planned purchases (issue #158), the household's side: a goal with an amount and a budget month, saved for in an envelope (a budget
item) or an account, and, once reached, whether it was bought. Stored as rows of `goals` with the dated columns of
db/migrations/0158_dated_goals.sql, keyed `purchase:<slug>`; the arithmetic is pipeline/purchases.py, run at every build.

Bought: the goal is marked done; on request a life event (kind purchase, #109) is added, and for a loan a debt is set up from the
prefilled form: a budget item with Flow debt and its type (#141) through pipeline/mapping_store.py, and a debt account with its
opening balance (#143) through pipeline/accounts_store.py. Nothing is created that the form did not ask for."""
from __future__ import annotations
import re
from decimal import Decimal

from psycopg.rows import dict_row

from . import db
from .yearly_bills import parse_amount

PREFIX = "purchase:"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
COLS = "key, label, note, target_amount, target_month, funded_by, funded_kind, since_month, done_at, done_month"


class PurchaseError(ValueError):
    """A purchase that cannot be saved, with the form field it is about, so the page can mark that field (DS-FORM-1)."""
    def __init__(self, message: str, field: str = "label"):
        super().__init__(message); self.field = field


def _x(conn, sql, params=()):
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-") or "goal"


def anchor(key: str) -> str:
    """The row's id on Settings and in the check-in: 'pp-' and the key's slug."""
    return "pp-" + slug(key[len(PREFIX):] if key.startswith(PREFIX) else key)


def stored(conn) -> list[dict]:
    rows = _x(conn, f"SELECT {COLS} FROM goals WHERE target_amount IS NOT NULL ORDER BY target_month, key").fetchall()
    return [{**r, "target_amount": float(r["target_amount"])} for r in rows]


def built(conn) -> list[dict]:
    r = _x(conn, "SELECT rows FROM datasets WHERE name='planned_purchases'").fetchone()
    return r["rows"] if r else []


def for_page(conn) -> list[dict]:
    """Each stored purchase with the last build's progress laid over it (none until a build has seen it)."""
    prog = {p["key"]: p for p in built(conn)}
    rows = stored(conn)
    out = []
    for s in rows:
        shared = [o["label"] for o in rows if o["key"] != s["key"] and o["funded_by"] == s["funded_by"] and not o["done_at"]]
        p = prog.get(s["key"]) or {}
        same = p and p.get("target_amount") == s["target_amount"] and p.get("target_month") == s["target_month"] and p.get("funded_by") == s["funded_by"]
        out.append({**s, "anchor": anchor(s["key"]), "done": bool(s["done_at"]), "progress": p if same else None,
                    "shared_with": shared if not s["done_at"] else []})   # each counts all of what is kept there: say so
    return out


def latest_month(conn) -> str | None:
    r = _x(conn, "SELECT max(budget_month) AS m FROM source_files").fetchone()
    return r["m"] if r else None


def debt_category(conn) -> str:
    """The category a new loan's payments are offered under: the first of the debt_groups rule (#162), read, never typed."""
    from .roles import ROLE_DEFAULTS
    groups = (db.get_setting(conn, "rules", {}) or {}).get("debt_groups")
    groups = ROLE_DEFAULTS["debt_groups"] if groups is None else groups
    return groups[0] if groups else ""


def fund_kind(conn, name: str) -> str | None:
    """envelope when the name is a budget (canonical) item, account when it is an account; an account wins only when it is not also an item."""
    if _x(conn, "SELECT 1 FROM canonical_items WHERE name=%s", (name,)).fetchone():
        return "envelope"
    if _x(conn, "SELECT 1 FROM accounts WHERE name=%s", (name,)).fetchone():
        return "account"
    return None


def save(conn, label: str, amount, month: str, funded_by: str, since: str | None = None, original: str = "", note: str = "") -> str:
    """Add or change a planned purchase; returns its key. `original` is the key of the one being changed (its label may change)."""
    label, month, funded_by, since = (label or "").strip(), (month or "").strip(), (funded_by or "").strip(), (since or "").strip() or None
    if not label:
        raise PurchaseError("say what the purchase is, such as Car down payment.", "label")
    try:
        amount = parse_amount(amount)
    except ValueError:
        raise PurchaseError("the amount is a number, such as 8000 or 8,000.00.", "amount") from None
    if amount is None or amount <= 0:
        raise PurchaseError("say how much it will take, more than zero.", "amount")
    if not MONTH_RE.match(month):
        raise PurchaseError("the month to have it by looks like 2028-03.", "month")
    if since and not MONTH_RE.match(since):
        raise PurchaseError("the month the saving starts looks like 2026-07, or leave it blank.", "since")
    if not funded_by:
        raise PurchaseError("say where the money is kept: a budget item (an envelope) or an account.", "funded_by")
    kind = fund_kind(conn, funded_by)
    if not kind:
        raise PurchaseError(f"{funded_by} is not one of your budget items or accounts. Pick one from the list.", "funded_by")
    key = original or PREFIX + slug(label)
    if not original and _x(conn, "SELECT 1 FROM goals WHERE key=%s", (key,)).fetchone():
        raise PurchaseError(f"there is already a goal called {label}. Give this one another name.", "label")
    since = since or (None if original else latest_month(conn))
    if original:
        if not _x(conn, "SELECT 1 FROM goals WHERE key=%s AND target_amount IS NOT NULL", (original,)).fetchone():
            raise PurchaseError(f"{label} is no longer a planned purchase.", "label")
        _x(conn, "UPDATE goals SET label=%s, note=%s, target_amount=%s, target_month=%s, funded_by=%s, funded_kind=%s, "
                 "since_month=COALESCE(%s, since_month), updated_at=now() WHERE key=%s",
           (label, note, amount, month, funded_by, kind, since, original))
    else:
        _x(conn, "INSERT INTO goals (key, value, unit, label, note, target_amount, target_month, funded_by, funded_kind, since_month) "
                 "VALUES (%s, NULL, 'usd', %s, %s, %s, %s, %s, %s, %s)", (key, label, note, amount, month, funded_by, kind, since))
    db.mark_inputs_changed(conn)
    return key


def delete(conn, key: str) -> int:
    n = _x(conn, "DELETE FROM goals WHERE key=%s AND target_amount IS NOT NULL", (key,)).rowcount
    if n:
        db.mark_inputs_changed(conn)
    return n


def mark_bought(conn, key: str, month: str, event: bool = False, loan: dict | None = None, by: str = "") -> dict:
    """Mark a planned purchase bought in `month`; with `event`, add the life event; with `loan` ({item, category, debt_type, account,
    balance, apr}), set up the debt. All or nothing: a problem raises PurchaseError before anything is written. Returns what was done."""
    g = _x(conn, f"SELECT {COLS} FROM goals WHERE key=%s AND target_amount IS NOT NULL", (key,)).fetchone()
    if not g:
        raise PurchaseError("that planned purchase is gone. Reload the page.", "label")
    if not MONTH_RE.match(month or ""):
        raise PurchaseError("the month it was bought looks like 2026-07.", "month")
    done = {"event": None, "debt": None, "account": None}
    if loan is not None:
        from pipeline import accounts_store, mapping_store
        item, cat = (loan.get("item") or "").strip(), (loan.get("category") or "").strip()
        dtype = (loan.get("debt_type") or "").strip() or None
        if not item:
            raise PurchaseError("name the budget item the loan's payments will go under, such as Car Loan.", "loan_item")
        if not cat:
            raise PurchaseError("say which category the loan's payments belong to, such as Debt.", "loan_category")
        if dtype and dtype not in mapping_store.DEBT_TYPES:
            raise PurchaseError("the debt type is one of " + ", ".join(mapping_store.DEBT_TYPES) + ".", "loan_type")
        try:
            balance = parse_amount(loan.get("balance"))
        except ValueError:
            raise PurchaseError("the amount borrowed is a number, such as 12000.", "loan_balance") from None
        acct, err = accounts_store.check_account({"name": loan.get("account") or item, "kind": "debt", "type": dtype or "", "debt_item": item,
                                                  "apr": loan.get("apr") or ""}, None)
        if err:
            f, m = next(iter(err.items()))
            raise PurchaseError(f"the loan's {f.replace('_', ' ')}: {m}.", "loan_apr" if f == "apr" else "loan_account")
        if _x(conn, "SELECT 1 FROM accounts WHERE name=%s", (acct["name"],)).fetchone():
            raise PurchaseError(f"there is already an account called {acct['name']}. Give the loan another name.", "loan_account")
        mine = mapping_store.categories_of(conn).get(item)
        if mine is None:   # a new budget item: one mapping row, the item to itself, Flow debt
            try:
                mapping_store.write_rows(conn, [{"item": item, "canonical_item": item, "category": cat, "flow": "debt",
                                                 "note": f"Added when {g['label']} was bought with a loan.", "debt_type": dtype}])
            except mapping_store.MappingError as e:
                raise PurchaseError(str(e), "loan_item") from None
        elif dtype:
            mapping_store.set_debt_type(conn, item, dtype)
        accounts_store.save_account(conn, acct)
        if balance:
            accounts_store.set_balance(conn, acct["name"], month, Decimal(str(balance)), "entered", by)
        done["debt"], done["account"] = item, acct["name"]
    if event:
        label = f"Bought {g['label']}"
        _x(conn, "INSERT INTO life_events (month, label, kind) VALUES (%s, %s, 'purchase') ON CONFLICT DO NOTHING", (month, label))
        done["event"] = label
    _x(conn, "UPDATE goals SET done_at=now(), done_month=%s, updated_at=now() WHERE key=%s", (month, key))
    db.mark_inputs_changed(conn)
    return done


def reopen(conn, key: str) -> int:
    """Not bought after all: the goal is open again. A life event or a debt set up when it was marked bought stays."""
    n = _x(conn, "UPDATE goals SET done_at=NULL, done_month=NULL, updated_at=now() WHERE key=%s AND target_amount IS NOT NULL", (key,)).rowcount
    if n:
        db.mark_inputs_changed(conn)
    return n
