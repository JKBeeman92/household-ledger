"""Step 6.2, The quarter (#147): on a quarter's (or the year's) last budget month, the Quarter tab read in place: this quarter against the
one before and the same quarter a year before, a month at a time, and what falls in the next. Read-only; opening it marks it read."""
from . import Step, register, _x


def _review(conn, month):
    r = _x(conn, "SELECT rows FROM datasets WHERE name='quarter_review'").fetchone()
    q = (r["rows"] or [None])[0] if r and r["rows"] else None
    return q if q and q.get("last") == month else None


def _applies(conn, month, mode, short):
    return mode in ("quarter", "year") and not short


def _status(conn, month, state):
    if "quarter" in (state.get("viewed") or {}):
        return "done", "read"
    return "todo", "open it to read the quarter" if _review(conn, month) else "rebuild to see the quarter, then read it"


def _context(conn, month, state):
    return {"q": _review(conn, month)}


register(Step("quarter", 62, "The quarter", _status, applies=_applies, context=_context, actions=("view",), issue="#147"))
