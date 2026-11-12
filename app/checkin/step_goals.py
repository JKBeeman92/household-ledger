"""Step 5, Goals to confirm: no goal waits for confirmation (the month-close checklist, app/routes/checklist.py)."""
from . import Step, register


def _status(conn, month, state):
    from app.routes.checklist import checklist_goals
    waiting = checklist_goals(conn)
    if not waiting:
        return "done", "no goal waits for confirmation"
    confirmed = [g for g in waiting if g["confirmed_at"] and g["value"] is not None and g["confirmed_at"].strftime("%Y-%m") > month]
    if len(confirmed) == len(waiting):
        return "done", f"{len(waiting)} confirmed since the month ended"
    return "todo", f"{len(waiting) - len(confirmed)} to confirm"


def _context(conn, month, state):
    from app.routes.checklist import checklist_goals
    return {"checklist": checklist_goals(conn)}


register(Step("goals", 50, "Goals to confirm", _status, context=_context))
