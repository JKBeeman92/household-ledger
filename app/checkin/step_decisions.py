"""Step 4, Last month's decisions (#155): each decision in force this month, with the app's own verdict where its target allows
("Eating Out was $372: kept") and the household's confirmation: kept, partly or not kept, or carry it on a month. Part of the short
form, so a catch-up checks each missed month in turn. Applies once a decision is in force."""
from . import Step, register


def _rows(conn, month):
    from app import decisions
    return decisions.in_force(conn, month)


def _applies(conn, month, mode, short):
    return bool(_rows(conn, month))


def _status(conn, month, state):
    rows = _rows(conn, month)
    left = [d for d in rows if not d["check"]]
    if not left:
        return "done", f"all {len(rows)} confirmed"
    return "todo", f"{len(rows) - len(left)} of {len(rows)} confirmed"


def _context(conn, month, state):
    from app import decisions
    rows = _rows(conn, month)
    return {"rows": rows, "words": decisions.VERDICT_WORDS}


register(Step("decisions", 40, "Last month's decisions", _status, applies=_applies, context=_context, in_short=True, issue="#155"))
