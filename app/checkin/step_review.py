"""Step 8, Review: the month-close review is written, or skipped. Optional: with no review provider configured it counts as skipped."""
from . import Step, register, _x


def _status(conn, month, state):
    if _x(conn, "SELECT 1 FROM reviews WHERE month=%s", (month,)).fetchone():
        return "done", "written"
    from app import review_providers
    if not review_providers.is_configured(conn):
        return "skipped", "no review provider configured (optional)"
    return "todo", "not written yet; write it, or skip"


def _context(conn, month, state):
    from app import review, review_providers
    j = review.latest_job(conn)
    return {"job": j if j and j.get("month") == month else None, "configured": review_providers.is_configured(conn),
            "written": _x(conn, "SELECT 1 FROM reviews WHERE month=%s", (month,)).fetchone() is not None}


register(Step("review", 80, "Review", _status, context=_context, actions=("skip",)))
