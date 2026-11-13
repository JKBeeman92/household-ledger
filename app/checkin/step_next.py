"""Step 9, Next month (#155): up to three decisions for the coming month, written down so the next check-in can check them. A row
of the Suggested plan can be taken as a plan change in one press. Saying there are no changes this month also completes the step. Only on the
full check-in: decisions are made at the latest month, not while catching up."""
from . import Step, register, _x


def _status(conn, month, state):
    from app import decisions
    n = len(decisions.made_in(conn, month))
    if n:
        return "done", f"{n} decision{'s' if n != 1 else ''} for next month"
    if "next" in (state.get("ack") or {}):
        return "done", "no changes this month"
    return "todo", "decide what to change next month, or say there is nothing to change"


def _context(conn, month, state):
    from app import decisions
    from pipeline import decisions as rules
    sug = _x(conn, "SELECT rows FROM datasets WHERE name='plan_suggestions'").fetchone()
    made = decisions.made_in(conn, month)
    taken = {(d.get("target") or {}).get("category") for d in made if d["kind"] == "plan_change"}
    rows = [r for r in (sug["rows"] if sug else []) if r["m"] == month and r["flow"] != "income" and abs(r["suggest"] - r["planned"]) >= 10 and r["category"] not in taken]
    cats = [r["name"] for r in _x(conn, "SELECT name FROM categories ORDER BY name").fetchall()]
    items = [r["name"] for r in _x(conn, "SELECT name FROM canonical_items ORDER BY name").fetchall()]
    debts = [r["name"] for r in _x(conn, "SELECT name FROM accounts WHERE kind='debt' AND active AND debt_item IS NOT NULL ORDER BY name").fetchall()]
    people = [p for p in ((_x(conn, "SELECT value FROM settings WHERE key='rules'").fetchone() or {}).get("value") or {}).get("people") or []]
    return {"made": made, "suggestions": sorted(rows, key=lambda r: -abs(r["suggest"] - r["planned"]))[:6], "categories": cats, "item_names": items,
            "debts": debts, "people": people, "kinds": decisions.KIND_WORDS, "cap": rules.SOFT_CAP, "next": rules.add_months(month, 1),
            "acked": "next" in (state.get("ack") or {})}


register(Step("next", 90, "Next month", _status, context=_context, actions=("ack",), issue="#155"))
