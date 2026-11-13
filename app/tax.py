"""Estimated tax at the check-in (issue #153, part 1), the household's side: the rules (which income has no tax withheld, where the tax is
set aside, the deadlines), the set-aside share as a goal confirmed at each month-close, and a quarter's payment ticked as paid. The
arithmetic is pipeline/tax.py, run at every build. With no contract income items, nothing is shown anywhere."""
from __future__ import annotations
import json, re

from psycopg.rows import dict_row

from . import db

GOAL = "tax_set_aside_pct"
GOAL_UNIT = "share of contract income"
GOAL_NOTE = "Ask at each month-close: the share of contract income set aside for estimated tax; it changes with income and tax law."
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _x(conn, sql, params=()):
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


def rules(conn) -> dict:
    r = db.get_setting(conn, "rules", {}) or {}
    return {"income_items": list(r.get("contract_income_items") or []), "aside_items": list(r.get("tax_set_aside_items") or []), "deadlines": list(r.get("tax_deadlines") or [])}


def enabled(conn) -> bool:
    return bool(rules(conn)["income_items"])


def summary(conn) -> dict | None:
    r = _x(conn, "SELECT rows FROM datasets WHERE name='tax'").fetchone()
    return (r["rows"] or [None])[0] if r and r["rows"] else None


def goal(conn) -> float | None:
    r = _x(conn, "SELECT value FROM goals WHERE key=%s", (GOAL,)).fetchone()
    return float(r["value"]) if r and r["value"] is not None else None


def parse_deadlines(text: str) -> list[dict]:
    """One per line, 'YYYY-MM label' (the label optional): 2026-09 Q3 federal estimate."""
    out = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        m, _, label = line.partition(" ")
        if not MONTH_RE.match(m):
            raise ValueError(f"line {n}: start with the month as YYYY-MM, then the label (2026-09 Q3 federal estimate)")
        out.append({"m": m, "label": label.strip() or "Estimated tax"})
    return sorted(out, key=lambda d: d["m"])


def save_settings(conn, items: list[str], aside: list[str], pct, deadlines: list[dict]) -> None:
    """The rules and the goal together, from Settings → Goals → Estimated tax. Blank items turn it all off (the goal is removed)."""
    names = {r["name"] for r in _x(conn, "SELECT name FROM canonical_items").fetchall()}
    accounts = {r["name"] for r in _x(conn, "SELECT name FROM accounts").fetchall()}
    bad = [i for i in items if i not in names]
    if bad:
        raise ValueError(f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} not one of your budget items")
    bad = [a for a in aside if a not in names and a not in accounts]
    if bad:
        raise ValueError(f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} neither a budget item nor an account")
    r = dict(db.get_setting(conn, "rules", {}) or {})
    r.update(contract_income_items=items, tax_set_aside_items=aside, tax_deadlines=deadlines)
    db.put_setting(conn, "rules", r)
    if not items or pct in (None, ""):
        _x(conn, "DELETE FROM goals WHERE key=%s", (GOAL,))
    else:
        from .routes.settings import parse_goal_value
        v, problem = parse_goal_value(str(pct), GOAL_UNIT)
        if problem or v is None:
            raise ValueError(f"the share set aside: {problem or 'give a percentage, such as 25%'}")
        _x(conn, "INSERT INTO goals (key, value, unit, label, note, confirmed_at) VALUES (%s,%s,%s,%s,%s, now()) ON CONFLICT (key) DO UPDATE "
                 "SET value=EXCLUDED.value, unit=EXCLUDED.unit, label=EXCLUDED.label, note=EXCLUDED.note, confirmed_at=now()",
           (GOAL, v, GOAL_UNIT, "Estimated tax set aside", GOAL_NOTE))
    db.mark_inputs_changed(conn)


def payment(conn, month: str) -> dict | None:
    return _x(conn, "SELECT month, deadline_m, label, expected, amount, paid_at, paid_by FROM tax_payments WHERE month=%s", (month,)).fetchone()


def mark_paid(conn, month: str, amount=None, by: str = "") -> None:
    """The quarter's estimated payment is paid; the deadline, label and expected amount are kept as they stand now."""
    if not MONTH_RE.match(month or ""):
        raise ValueError("a payment is recorded at a quarter's check-in (YYYY-MM)")
    try:
        amount = None if amount in (None, "") else round(float(re.sub(r"[^\d.\-]", "", str(amount).replace(",", ""))), 2)
    except ValueError:
        raise ValueError("the amount paid is a number, such as 1200") from None
    if amount is not None and amount < 0:
        raise ValueError("the amount paid is not negative")
    s = summary(conn) or {}
    _x(conn, "INSERT INTO tax_payments (month, deadline_m, label, expected, amount, paid_by) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (month) DO UPDATE "
             "SET amount=EXCLUDED.amount, paid_at=now(), paid_by=EXCLUDED.paid_by",
       (month, s.get("next_deadline"), s.get("next_label") or "", s.get("quarter_expected"), amount, by or ""))
    db.mark_inputs_changed(conn)


def unmark(conn, month: str) -> None:
    _x(conn, "DELETE FROM tax_payments WHERE month=%s", (month,))
    db.mark_inputs_changed(conn)


def export_json(conn) -> str:
    rows = _x(conn, "SELECT month, deadline_m, label, expected, amount, note FROM tax_payments ORDER BY month").fetchall()
    return json.dumps([{**r, **{k: float(r[k]) if r[k] is not None else None for k in ("expected", "amount")}} for r in rows], indent=1)
