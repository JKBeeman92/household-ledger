"""The decisions log (issue #155): add a decision at a check-in, confirm each month it was in force, carry one on, and read the
log back. The rules (months in force, the app's own verdict) are pipeline/decisions.py, shared with the build's `decisions` dataset.

The app's verdict is worked out on read from the built `budget_items`, so a month whose budget file is not yet loaded and built
reads `pending`, never `dropped`. When the household confirms a month, the app's verdict and the figure it rested on are kept with
it (`auto_verdict`, `auto_figure`), so a later rebuild cannot change what was agreed at the table. A decision's free text is the
household's own words; its `note` is private and is never sent to the review provider or MCP."""
from __future__ import annotations
import json
import re

from psycopg.rows import dict_row

from pipeline import decisions as rules

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KIND_WORDS = {"spend_cap": "Spend no more than", "extra_payment": "Pay extra on a debt", "save": "Put in at least",
              "plan_change": "Change next month's plan", "other": "Something else"}
VERDICT_WORDS = {"kept": "kept", "partly": "partly kept", "dropped": "not kept", "pending": "waiting for the month's files", "unknown": "yours to say"}


def _x(conn, sql, params=()):
    cur = conn.cursor(row_factory=dict_row); cur.execute(sql, params); return cur


def _rule(conn, key: str, default):
    r = _x(conn, "SELECT value FROM settings WHERE key='rules'").fetchone()
    v = (r["value"] if r else {}) or {}
    return v.get(key) if isinstance(v, dict) and v.get(key) is not None else default


def _dataset(conn, name: str) -> list[dict]:
    r = _x(conn, "SELECT rows FROM datasets WHERE name=%s", (name,)).fetchone()
    return r["rows"] if r else []


def all_decisions(conn) -> list[dict]:
    return _x(conn, "SELECT id, made_in, text, kind, target, until, person, status, note, created_at, created_by FROM decisions ORDER BY made_in, id").fetchall()


def checks(conn) -> dict:
    return {(r["decision_id"], r["month"]): r for r in _x(conn, "SELECT decision_id, month, verdict, auto_verdict, auto_figure, checked_at, checked_by FROM decision_checks").fetchall()}


def live(conn, d: dict, month: str) -> tuple[str, float | None]:
    """The app's verdict for decision d in month X, from the built budget rows (pending when X is not built)."""
    rows = [r for r in _dataset(conn, "budget_items") if r["m"] == month]
    return rules.auto(d["kind"], d.get("target"), rows or None, float(_rule(conn, "decision_partly_over", rules.PARTLY_OVER)),
                      float(_rule(conn, "decision_partly_share", rules.PARTLY_SHARE)))


def in_force(conn, month: str) -> list[dict]:
    """The decisions checked at month X's check-in: in force in X, with the app's verdict and any confirmation."""
    cs, out = checks(conn), []
    for d in all_decisions(conn):
        if month in rules.months_in_force(d, month):
            c = cs.get((d["id"], month))
            v, x = (c["auto_verdict"], float(c["auto_figure"]) if c["auto_figure"] is not None else None) if c else live(conn, d, month)
            out.append({**d, "month": month, "auto_verdict": v, "auto_figure": x, "check": c, "last_month": rules.months_in_force(d, "9999-12")[-1] == month})
    return out


def made_in(conn, month: str) -> list[dict]:
    return [d for d in all_decisions(conn) if d["made_in"] == month]


def auto_text(d: dict, money=lambda v: f"${v:,.0f}") -> str:
    """'Eating Out was $372: kept', from the decision and its verdict."""
    t, v, x = d.get("target") or {}, d.get("auto_verdict"), d.get("auto_figure")
    name = t.get("category") or t.get("item") or t.get("debt") or ""
    if v == "pending":
        return "Waiting for the month's files: the app checks it once they are loaded and built."
    if v == "unknown" or x is None:
        return "The app cannot check this one: say how it went." if not t else f"{name} is not in the month's budget, so the app cannot check it."
    what = {"plan_change": f"{name} was planned at {money(x)}", "extra_payment": f"{money(x)} was paid to {name}"}.get(d["kind"], f"{name} was {money(x)}")
    return f"{what}: {VERDICT_WORDS[v]}."


# ---- writing ----

def _norm_target(conn, kind: str, target: dict | None, made: str) -> dict | None:
    """A target as stored: names checked against the mapping, amounts as numbers, and what each kind needs frozen at the start
    (an extra payment's usual payment, a plan change's current plan)."""
    if kind == "other" or not target:
        return None
    t = {k: v for k, v in target.items() if v not in (None, "")}
    for k in ("max", "min", "extra", "planned", "base", "from"):
        if k in t:
            t[k] = round(float(t[k]), 2)
    if kind == "extra_payment" and t.get("debt") and not t.get("item"):
        a = _x(conn, "SELECT debt_item FROM accounts WHERE name=%s", (t["debt"],)).fetchone()
        if not a or not a["debt_item"]:
            raise DecisionError(f"the account {t['debt']} has no linked budget item to check payments against", "what")
        t["item"] = a["debt_item"]
    if t.get("category") and not _x(conn, "SELECT 1 FROM categories WHERE name=%s", (t["category"],)).fetchone():
        raise DecisionError(f"there is no category {t['category']}", "what")
    if t.get("item") and not _x(conn, "SELECT 1 FROM canonical_items WHERE name=%s", (t["item"],)).fetchone():
        raise DecisionError(f"there is no budget item {t['item']}", "what")
    need = {"spend_cap": "max", "save": "min", "extra_payment": "extra", "plan_change": "planned"}[kind]
    if need not in t:
        raise DecisionError({"max": "give the most to spend", "min": "give the least to put in", "extra": "give the extra to pay",
                             "planned": "give the new planned amount"}[need], "amount")
    if not (t.get("category") or t.get("item")):
        raise DecisionError("say which category or budget item it is about", "what")
    rows = _dataset(conn, "budget_items")
    if kind == "extra_payment" and "base" not in t:
        t["base"] = rules.usual_payment(rows, t["item"], rules.add_months(made, 1))
    if kind == "plan_change" and "from" not in t:
        cur = rules.figure(t, [r for r in rows if r["m"] == made], "planned")
        if cur is not None:
            t["from"] = cur
    return t


class DecisionError(ValueError):
    """A decision that cannot be saved, with the Next month form's field it is about (DS-FORM-1), as TaxError does."""
    def __init__(self, message: str, field: str = "text"):
        super().__init__(message); self.field = field


def add(conn, made: str, text: str, kind: str = "other", target: dict | None = None, until: str | None = None,
        person: str | None = None, note: str = "", by: str = "") -> tuple[int, int]:
    """A new decision made at month `made`'s check-in. Returns (its id, how many were made that month), so the page can say three
    is usually enough once a fourth arrives (the soft cap, rules.SOFT_CAP)."""
    if not MONTH_RE.match(made or ""):
        raise DecisionError("a decision is made at a month's check-in (YYYY-MM)", "text")
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        raise DecisionError("say what you decided, in a few words", "text")
    if len(text) > 300:
        raise DecisionError("keep it under 300 characters", "text")
    if kind not in rules.KINDS:
        raise DecisionError(f"kind is one of {', '.join(rules.KINDS)}", "kind")
    until = (until or "").strip() or None
    if until:
        if not MONTH_RE.match(until):
            raise DecisionError("until is a month (YYYY-MM)", "until")
        if until <= made:
            raise DecisionError("until is a month after the check-in", "until")
        if until == rules.add_months(made, 1):
            until = None   # one month: the default
    t = _norm_target(conn, kind, target, made)
    r = _x(conn, "INSERT INTO decisions (made_in, text, kind, target, until, person, note, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
           (made, text, kind, json.dumps(t) if t else None, until, (person or "").strip() or None, note or "", by or "")).fetchone()
    return r["id"], len(made_in(conn, made))


def confirm(conn, decision_id: int, month: str, verdict: str, by: str = "") -> dict:
    """The household's verdict on month X: kept, partly or dropped. Keeps the app's verdict and figure as they stood, and closes the
    decision when X was its last month in force (a payoff decision closes as done once its debt is paid off)."""
    if verdict not in rules.VERDICTS:
        raise ValueError(f"the verdict is one of {', '.join(rules.VERDICTS)}")
    d = next((x for x in in_force(conn, month) if x["id"] == decision_id), None)
    if not d:
        raise ValueError("that decision is not in force in that month")
    _x(conn, "INSERT INTO decision_checks (decision_id, month, verdict, auto_verdict, auto_figure, checked_by) VALUES (%s,%s,%s,%s,%s,%s) "
             "ON CONFLICT (decision_id, month) DO UPDATE SET verdict=EXCLUDED.verdict, checked_at=now(), checked_by=EXCLUDED.checked_by",
       (decision_id, month, verdict, d["auto_verdict"], d["auto_figure"], by or ""))
    t = d.get("target") or {}
    if t.get("until_payoff"):
        po = next((p for p in _dataset(conn, "payoff") if p.get("account") == t.get("debt")), None)
        if po and po.get("status") == "paid_off":
            _x(conn, "UPDATE decisions SET status='done' WHERE id=%s", (decision_id,))
    elif d["last_month"]:
        _x(conn, "UPDATE decisions SET status=%s WHERE id=%s", (verdict, decision_id))
    return d


def carry_on(conn, decision_id: int, month: str) -> str:
    """Keep a decision in force one more month after X (its `until` moves on); it reopens if X had closed it."""
    nxt = rules.add_months(month, 1)
    r = _x(conn, "UPDATE decisions SET until=%s, status='open' WHERE id=%s AND (until IS NULL OR until < %s) RETURNING id", (nxt, decision_id, nxt)).fetchone()
    if not r:
        raise ValueError("that decision already runs past next month")
    return nxt


def delete(conn, decision_id: int) -> None:
    _x(conn, "DELETE FROM decisions WHERE id=%s", (decision_id,))


def rewrite_targets(conn, dimension: str, old: str, new: str) -> int:
    """A rename reaches decisions too (#155): a target names a category, a canonical item or (an extra payment's) an account by name,
    in JSON that no foreign key follows. Called in the rename's own transaction."""
    if dimension in ("canonical_item", "account"):   # a yearly bill's funding names an envelope or an account (#151)
        from . import yearly_bills
        yearly_bills.rename_fund(conn, old, new)
    key = {"category": "category", "canonical_item": "item", "account": "debt"}[dimension]
    return _x(conn, "UPDATE decisions SET target = jsonb_set(target, %s, to_jsonb(%s::text)) WHERE target->>%s = %s",
              ("{" + key + "}", new, key, old)).rowcount


# ---- settings.zip and the seed loader ----

def export_json(conn) -> str:
    cs = checks(conn)
    out = []
    for d in all_decisions(conn):
        mine = [{"month": m, "verdict": c["verdict"], "auto_verdict": c["auto_verdict"], "auto_figure": float(c["auto_figure"]) if c["auto_figure"] is not None else None}
                for (i, m), c in sorted(cs.items()) if i == d["id"]]
        out.append({k: d[k] for k in ("made_in", "text", "kind", "target", "until", "person", "status", "note")} | {"checks": mine})
    return json.dumps(out, indent=1)
