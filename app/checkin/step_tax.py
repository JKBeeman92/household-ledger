"""Step 6.5, Estimated tax (#153): on a quarter's (or the year's) last budget month, the quarter's estimated payment: the next deadline,
the amount expected from the quarter's contract income, and a tick that it was paid. Only when contract income items are set."""
from . import Step, register


def _applies(conn, month, mode, short):
    from app import tax
    return mode in ("quarter", "year") and not short and tax.enabled(conn)


def _status(conn, month, state):
    from app import tax
    p = tax.payment(conn, month)
    if p:
        return "done", "paid"   # the amount is in the step, formatted for the household
    if "tax" in (state.get("ack") or {}):
        return "done", "nothing due this quarter"
    return "todo", "pay this quarter's estimate, or say nothing is due"


def _context(conn, month, state):
    from app import tax
    return {"s": tax.summary(conn), "paid": tax.payment(conn, month), "acked": "tax" in (state.get("ack") or {}), "pct": tax.goal(conn)}


register(Step("tax", 65, "Estimated tax", _status, applies=_applies, context=_context, actions=("ack",), issue="#153"))
