"""Step 6, Where we stand: a read-only summary of the month (the Last month tab's figures); reading it marks it done."""
from . import Step, register, _x


def _status(conn, month, state):
    return ("done", "read") if "standing" in (state.get("viewed") or {}) else ("todo", "open it to read the month's figures")


def _context(conn, month, state):
    ds = {r["name"]: r["rows"] for r in _x(conn, "SELECT name, rows FROM datasets WHERE name IN "
                                                 "('monthly','net_worth','debt_to_income','emergency_fund')").fetchall()}
    pick = lambda name: next((r for r in ds.get(name, []) if r["m"] == month), None)
    t = _x(conn, "SELECT rows FROM datasets WHERE name='tax'").fetchone()   # #153: every check-in shows the year's set-aside
    tx = next((r for r in (t["rows"] if t else []) if r["m"] == month), None)
    return {"monthly": pick("monthly"), "nw": pick("net_worth"), "dti": pick("debt_to_income"), "ef": pick("emergency_fund"), "tax": tx}


register(Step("standing", 60, "Where we stand", _status, context=_context, actions=("view",)))
