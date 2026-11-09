"""Reviews page: the household's editable review instructions (system prompt) and a no-call preview of exactly what would be sent."""
import re

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth, review
from ..web_helpers import flash, render

router = APIRouter()
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_LEN = 20000


@router.get("/reviews/prompt", response_class=HTMLResponse)
def prompt_page(request: Request, month: str = "", user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        text = review.instructions(conn)
        saved = isinstance(db.get_setting(conn, review.SETTING, None), str) and text != review.SYSTEM
        row = conn.execute("SELECT rows FROM datasets WHERE name='monthly'").fetchone()
        months = sorted((r["m"] for r in row["rows"]), reverse=True) if row else []
        context, error, tokens = None, None, None
        if month:
            try:
                context = review.prompt_for(conn, month) if MONTH.match(month) else None
                tokens = review.estimate_tokens(conn, month) if context else None
                error = None if context else "Pick a budget month from the list."
            except ValueError as e:
                error = f"{e}. Upload that month and rebuild first."
    return render(request, "reviews_prompt.html", user, active="reviews", text=text, customised=saved, months=months, month=month, context=context, tokens=tokens, error=error, default=review.SYSTEM)


@router.post("/reviews/prompt")
def prompt_save(request: Request, instructions: str = Form(""), user=Depends(auth.require_editor)):
    text = instructions.replace("\r\n", "\n").strip()
    if len(text) > MAX_LEN:
        flash(request, f"Not saved: instructions are limited to {MAX_LEN:,} characters.", "bad")
        return RedirectResponse("/reviews/prompt", status_code=303)
    with db.pool.connection() as conn:
        if not text or text == review.SYSTEM:   # blank or identical to the built-in means "use the built-in"
            conn.execute("DELETE FROM settings WHERE key=%s", (review.SETTING,))
            flash(request, "Using the built-in instructions.")
        else:
            db.put_setting(conn, review.SETTING, text)
            flash(request, "Saved. Every provider will use these instructions from the next review.")
        conn.commit()
    return RedirectResponse("/reviews/prompt", status_code=303)


@router.post("/reviews/prompt/reset")
def prompt_reset(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM settings WHERE key=%s", (review.SETTING,)); conn.commit()
    flash(request, "Reset to the built-in instructions.")
    return RedirectResponse("/reviews/prompt", status_code=303)
