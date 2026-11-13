"""The monthly check-in page (#164): /checkin/YYYY-MM, its step actions, Done and Reopen. The steps live in app/checkin/."""
from __future__ import annotations
import datetime as dt
import re

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import db, auth, hooks, checkin
from ..web_helpers import flash, render, month_text

router = APIRouter()


def _who(user) -> str:
    return (user or {}).get("email") or (user or {}).get("name") or ""


def _month(m: str) -> str:
    if not checkin.MONTH_RE.match(m or ""):
        raise HTTPException(404)
    return m


_FOCUS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def back_to(month: str, step: str, focus: str | None = None) -> RedirectResponse:
    """Back to a step after an action on it (DS-FLASH-1). `focus` names the control that took the place of the one used, so focus
    stays where the household was working (DS-A11Y-5): the page opens the step and focuses it, or the step's summary if it is gone."""
    frag = f"step-{step}" + (f"/{focus}" if focus and _FOCUS_RE.match(focus) else "")
    return RedirectResponse(f"/checkin/{month}#{frag}", status_code=303)


def month_name(m: str) -> str:
    return month_text(m)


@router.get("/checkin")
def checkin_latest(user=Depends(auth.require_editor)):
    """The latest loaded month's check-in; with nothing loaded, last calendar month's (whose first step is the upload)."""
    with db.pool.connection() as conn:
        m = checkin.latest(conn)
    if not m:
        t = dt.date.today().replace(day=1) - dt.timedelta(days=1)
        m = f"{t.year}-{t.month:02d}"
    return RedirectResponse(f"/checkin/{m}", status_code=303)


@router.get("/checkin/{month}", response_class=HTMLResponse)
def checkin_page(month: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        ov = checkin.overview(conn, month)
        latest = checkin.latest(conn)
        earlier, later = checkin.neighbours(conn, month)
    first_open = next((s["key"] for s in ov["steps"] if s["status"] in ("todo", "attention")), None)
    return render(request, "checkin.html", user, active="checkin", ov=ov, month_name=month_name, first_open=first_open, latest=latest, earlier=earlier, later=later,
                  next_url=f"/checkin/{month}")


@router.post("/checkin/{month}/steps/{step}/{action}")
def checkin_step(month: str, step: str, action: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        try:
            checkin.record(conn, month, step, action); conn.commit()
        except ValueError as e:
            conn.rollback()
            raise HTTPException(400, str(e))
    if "application/json" in (request.headers.get("accept") or ""):
        return Response(status_code=204)   # the page marks a read-only step read when it is opened
    if step == "next" and action == "ack":   # DS-FLASH-1: say what was recorded, and what was undone
        flash(request, f"Recorded: no changes for {month_text(month)}.")
    elif step == "next" and action == "undo":
        flash(request, "Undone: the Next month step is open again.")
    return back_to(month, step, {"ack": f"{step}-undo", "undo": f"{step}-ack"}.get(action))   # an ack is replaced by its undo, and back


@router.post("/checkin/{month}/complete")
def checkin_complete(month: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        try:
            ov = checkin.complete(conn, month, _who(user)); conn.commit()
        except ValueError as e:
            conn.rollback(); flash(request, f"Not done yet — {e}.", "bad")
            return RedirectResponse(f"/checkin/{month}", status_code=303)
        hooks.notify_async(conn, "checkin.completed", month, mode=ov["mode"], steps={s["key"]: s["status"] for s in ov["steps"]})
        nxt, latest = checkin.missing(conn), checkin.latest(conn)
    then = nxt[0] if nxt else latest if latest and latest > month else month   # catch-up runs oldest first, then this month
    flash(request, f"The {month_name(month)} check-in is done." + (f" Next to catch up: {month_name(nxt[0])}." if nxt else
                   f" On to {month_name(latest)}." if then != month else ""))
    return RedirectResponse(f"/checkin/{then}", status_code=303)


@router.post("/checkin/{month}/reopen")
def checkin_reopen(month: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        checkin.reopen(conn, month); conn.commit()
    flash(request, f"The {month_name(month)} check-in is open again; its first completion date is kept.")
    return RedirectResponse(f"/checkin/{month}", status_code=303)
