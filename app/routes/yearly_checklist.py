"""The yearly checklist (#157): tick or untick an item in the check-in's year mode, and keep the list on Settings → Yearly checklist
(add, rename, move, remove). Each action returns to the item it was made on (DS-FLASH-1, DS-A11Y-5)."""
from __future__ import annotations
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import auth, db, yearly_checklist as yc
from ..checkin import MONTH_RE
from ..checkin.step_year_review import year_label
from ..web_helpers import flash
from .checkin import _who

router = APIRouter()


def _settings(focus: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?focus={quote(focus)}#yearly-checklist", status_code=303)


@router.post("/checkin/{month}/yearly/{key}/tick")
async def tick(month: str, key: str, request: Request, user=Depends(auth.require_editor)):
    if not MONTH_RE.match(month):
        raise HTTPException(404)
    f = await request.form()
    with db.pool.connection() as conn:
        try:
            yc.tick(conn, year_label(conn, month), key, str(f.get("note") or ""), _who(user)); conn.commit()
            flash(request, "Ticked off.")
        except yc.ChecklistError as e:
            conn.rollback(); flash(request, f"Not ticked: {e}", "bad")
    return RedirectResponse(f"/checkin/{month}#yc-{quote(key)}", status_code=303)


@router.post("/checkin/{month}/yearly/{key}/untick")
def untick(month: str, key: str, request: Request, user=Depends(auth.require_editor)):
    if not MONTH_RE.match(month):
        raise HTTPException(404)
    with db.pool.connection() as conn:
        yc.untick(conn, year_label(conn, month), key); conn.commit()
    flash(request, "Not done after all: it is back on the list for this year.")
    return RedirectResponse(f"/checkin/{month}#yc-{quote(key)}", status_code=303)


@router.post("/yearly-checklist")
async def add(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    with db.pool.connection() as conn:
        try:
            key = yc.add(conn, str(f.get("label") or "")); conn.commit()
        except yc.ChecklistError as e:
            conn.rollback(); flash(request, f"Not added: {e}", "bad")
            request.session["yc_form"] = {"label": str(f.get("label") or ""), "error": str(e)}
            return _settings("yc-add")
    flash(request, f"Added {str(f.get('label')).strip()} to the yearly checklist.")
    return _settings(f"yci-{key}")


@router.post("/yearly-checklist/{key}/rename")
async def rename(key: str, request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    with db.pool.connection() as conn:
        try:
            yc.rename(conn, key, str(f.get("label") or "")); conn.commit(); flash(request, "Saved the new wording.")
        except yc.ChecklistError as e:
            conn.rollback(); flash(request, f"Not saved: {e}", "bad")
    return _settings(f"yci-{key}")


@router.post("/yearly-checklist/{key}/move")
async def move(key: str, request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    with db.pool.connection() as conn:
        yc.move(conn, key, -1 if f.get("dir") == "up" else 1); conn.commit()
    return _settings(f"yci-{key}")


@router.post("/yearly-checklist/{key}/remove")
def remove(key: str, request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        keys = [r["key"] for r in yc.items(conn)]
        label = next((r["label"] for r in yc.items(conn) if r["key"] == key), key)
        yc.remove(conn, key); conn.commit()
    flash(request, f"Removed {label}. Past years keep their ticks.")
    rest = [k for k in keys if k != key]
    if key in keys and rest:
        return _settings(f"yci-{rest[min(keys.index(key), len(rest) - 1)]}")
    return _settings("yc-add")
