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


def _pace_from(last: str, n: int) -> str:
    """The first of the n budget months ending at `last` ("2026-06", 5 -> "2026-02"): where the new normal's pace starts."""
    y, m = map(int, last.split("-")); i = y * 12 + m - 1 - (n - 1)
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _context(conn, month, state):
    y = _review(conn, month)
    a = (y or {}).get("ahead")
    if a and a.get("pace_since") and a.get("pace_months") and not a.get("pace_from"):   # the month, as the quarter and the dashboard say it
        y = {**y, "ahead": {**a, "pace_from": _pace_from(y["last"], a["pace_months"])}}
    return {"y": y}


register(Step("year_review", 63, "Year in review", _status, applies=_applies, context=_context, actions=("view",), issue="#157"))
