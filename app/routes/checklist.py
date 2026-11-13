"""Month-close checklist: goals that need asking (no value yet, or a note saying to ask) get an inline confirm/update form."""
from __future__ import annotations
import math, re
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form
from fastapi.responses import RedirectResponse

from .. import db, auth
from .settings import parse_goal_value
from ..web_helpers import flash
from ..builds import run_background
from pipeline.run_pipeline import build_running

router = APIRouter()
ASK = re.compile(r"\bask", re.I)


def checklist_goals(conn) -> list[dict]:
    """Goals that get a prompt: value is null, or the note says to ask. Oldest confirmation first."""
    rows = conn.execute("SELECT key, value, unit, label, note, confirmed_at FROM goals ORDER BY confirmed_at NULLS FIRST, key").fetchall()
    from .. import tax   # the estimated-tax share is asked only while contract income items are set (#153)
    off = not tax.enabled(conn)
    return [r for r in rows if (r["value"] is None or ASK.search(r["note"] or "")) and not (off and r["key"] == tax.GOAL)]


@router.post("/goals/{key}/confirm")
def confirm_goal(key: str, request: Request, background: BackgroundTasks, value: str = Form(""), next: str = Form(""), user=Depends(auth.require_editor)):
    back = RedirectResponse(auth._safe_next(next) if next else "/upload#checklist", status_code=303)   # the check-in's Goals step returns to it (#164)
    typed, value = value, value.strip()
    request.session.pop("goal_form", None)

    def refuse(msg: str, field_msg: str):   # the flash says it, and so does the field, which keeps what was typed and gets the page (DS-FORM-1)
        flash(request, msg, "bad")
        request.session["goal_form"] = {"key": key, "value": typed, "error": field_msg}
        return RedirectResponse(auth._safe_next((next or "/upload").split("#")[0]) + f"#goal-{key}", status_code=303)
    with db.pool.connection() as conn:
        g = conn.execute("SELECT label, value, unit FROM goals WHERE key=%s", (key,)).fetchone()
        if not g:
            flash(request, f"No goal {key!r}.", "bad"); return back
        if value:
            v, problem = parse_goal_value(value, g["unit"] or "")   # the same rule as the Settings editor
            if problem or v is None:
                return refuse(f"{g['label']}: {problem or 'enter a number'}.", problem or "enter a number")
            new = Decimal(str(v))
        elif g["value"] is None:
            return refuse(f"{g['label']}: enter a number.", "enter a number")
        else:
            new = g["value"]
        changed = new != g["value"]
        conn.execute("UPDATE goals SET value=%s, confirmed_at=now(), updated_at=now() WHERE key=%s", (new, key))
        conn.commit()
        busy = build_running(conn)
    if not changed:
        flash(request, f"{g['label']}: confirmed unchanged.")
    elif busy:
        flash(request, f"{g['label']}: updated to {new.normalize():f}. A rebuild is already running; press Rebuild when it finishes to refresh the dashboard.", "bad")
    else:
        flash(request, f"{g['label']}: updated to {new.normalize():f}. Rebuilding in the background.")
        background.add_task(run_background)
    return back
