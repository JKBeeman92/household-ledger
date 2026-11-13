"""Step 6.7, Yearly checklist (#157): on the year's last budget month, the household's once-a-year housekeeping (Settings → Yearly
checklist). Each item is ticked, with a note, for the budget year; Done when every item on the list is ticked, or the step is skipped."""
from . import Step, register
from .step_year_review import year_label


def _applies(conn, month, mode, short):
    return mode == "year" and not short


def _status(conn, month, state):
    from app import yearly_checklist
    done, n = yearly_checklist.status(conn, year_label(conn, month))
    if n and done == n:
        return "done", f"all {n} ticked"
    if "yearly" in (state.get("skipped") or {}):
        return "skipped", f"{done} of {n} ticked"
    return "todo", f"{done} of {n} ticked" if n else "the list is empty: add items on Settings → Yearly checklist"


def _context(conn, month, state):
    from app import yearly_checklist
    y = year_label(conn, month)
    return {"year": y, "list": yearly_checklist.items(conn), "ticks": yearly_checklist.ticks(conn, y)}


register(Step("yearly", 67, "Yearly checklist", _status, applies=_applies, context=_context, actions=("skip",), issue="#157"))
