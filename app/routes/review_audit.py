"""Review cost guard, audit list and hand-editing: confirm before a provider call, list past reviews with prompt and model, correct stored sections."""
import json, re
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth, review, review_providers as rp
from ..web_helpers import flash, render, month_text

router = APIRouter()
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/reviews/confirm", response_class=HTMLResponse)
def review_confirm(request: Request, month: str = "", back: str = "", user=Depends(auth.require_editor)):
    if not MONTH.match(month):
        flash(request, "Pick a budget month first.", "bad")
        return RedirectResponse("/upload", status_code=303)
    with db.pool.connection() as conn:
        prov = rp.resolve(conn)
        try:
            tokens, error = review.estimate_tokens(conn, month), None
        except ValueError as e:
            tokens, error = 0, str(e)
        existing = conn.execute("SELECT model, generated_at, hand_edited_at FROM reviews WHERE month=%s", (month,)).fetchone()
    return render(request, "reviews_confirm.html", user, active="reviews", month=month, prov=prov, problem=prov.problem(), tokens=tokens, error=error, existing=existing,
                  back=auth._safe_next(back) if back else "/upload")   # Cancel returns where the person came from (DS-FLASH-1)


@router.get("/reviews/history", response_class=HTMLResponse)
def review_history(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        rows = conn.execute("SELECT month, model, generated_at, hand_edited_at, sections, prompt FROM reviews ORDER BY month DESC").fetchall()
    return render(request, "reviews_history.html", user, active="reviews", reviews=rows)


def _get(conn, month: str):
    row = conn.execute("SELECT month, model, sections, hand_edited_at FROM reviews WHERE month=%s", (month,)).fetchone() if MONTH.match(month) else None
    if not row:
        raise HTTPException(404, "No review stored for that month")
    return row


@router.get("/reviews/edit/{month}", response_class=HTMLResponse)
def review_edit(request: Request, month: str, back: str = "", user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        row = _get(conn, month)
    typed = request.session.pop("review_edit", None)
    typed = typed["sections"] if typed and typed.get("month") == month else None
    return render(request, "reviews_edit.html", user, active="reviews", r=row, typed=typed, back=auth._safe_next(back) if back else "/reviews")


@router.post("/reviews/edit/{month}")
def review_edit_save(request: Request, month: str, heading: list[str] = Form(default=[]), text: list[str] = Form(default=[]), back: str = Form(""), user=Depends(auth.require_editor)):
    pairs = [(h.strip(), t.strip()) for h, t in zip(heading, text) if h.strip() or t.strip()]
    if not pairs or any(not h or not t for h, t in pairs):
        flash(request, "Not saved: every section needs both a heading and text, and at least one section must remain.", "bad")
        # what was typed comes back, with the half-filled sections marked, and the way back kept (DS-FORM-1)
        request.session["review_edit"] = {"month": month, "sections": [{"heading": h, "text": t, "bad": bool(h) != bool(t)} for h, t in zip(heading, text)]}
        return RedirectResponse(f"/reviews/edit/{month}" + (f"?back={quote(back)}" if back else ""), status_code=303)
    sections = [{"order": i, "heading": h, "text": t} for i, (h, t) in enumerate(pairs, 1)]
    with db.pool.connection() as conn:
        _get(conn, month)
        conn.execute("UPDATE reviews SET sections=%s::jsonb, hand_edited_at=now() WHERE month=%s", (json.dumps(sections), month))
        review.refresh_dataset(conn)
        conn.commit()
    flash(request, f"Saved your edits to the {month_text(month)} review. It is marked as hand-edited.")
    return RedirectResponse(auth._safe_next(back) if back else "/reviews", status_code=303)   # back where the Edit was pressed
