"""Step 6.3, Year in review (#157): on the year's last budget month, the Quarter tab's year view read in place: the year against the
year before, net worth and debts over it, the decisions kept, its life events, the categories that moved most, and where next year
leads (#148). Read-only; opening it marks it read."""
from . import Step, register, _x


def year_label(conn, month: str) -> str:
    from pipeline.years import year_of
    from . import year_start_month
    return year_of(month, year_start_month(conn))[0]


def _review(conn, month):
    r = _x(conn, "SELECT rows FROM datasets WHERE name='year_review'").fetchone()
    y = (r["rows"] or [None])[0] if r and r["rows"] else None
    return y if y and y.get("last") == month else None


def _applies(conn, month, mode, short):
    return mode == "year" and not short


def _status(conn, month, state):
    if "year_review" in (state.get("viewed") or {}):
        return "done", "read"
    return "todo", "open it to read the year" if _review(conn, month) else "rebuild to see the year, then read it"


def _context(conn, month, state):
    return {"y": _review(conn, month)}


register(Step("year_review", 63, "Year in review", _status, applies=_applies, context=_context, actions=("view",), issue="#157"))
