"""Decisions (#155) at the check-in: add one for next month, confirm a month, carry one on, remove one. Every action returns to the
step it was made on (DS-FLASH-1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import auth, db, decisions
from ..checkin import MONTH_RE
from ..web_helpers import flash, month_text
from .checkin import _month, _who, back_to

router = APIRouter()


def _back(month: str, step: str, focus: str | None = None) -> RedirectResponse:
    return back_to(month, step, focus)


def _field_of(msg: str) -> str | None:
    """Which field of the Next month form an error from decisions.add or _target is about, so it shows under that field (DS-FORM-1)."""
    for field, words in (("amount", ("give the", "number of dollars")), ("what", ("account", "category", "budget item")),
                         ("text", ("say what you decided", "300 characters")), ("until", ("until",)), ("kind", ("kind is",))):
        if any(w in msg for w in words):
            return field
    return None


def _target(form) -> dict | None:
    """The form's target: one of 'category:<name>', 'item:<name>' or 'debt:<account>', and an amount for the kind."""
    what, amount, kind = str(form.get("what") or ""), str(form.get("amount") or "").strip(), str(form.get("kind") or "other")
    if kind == "other" or not what:
        return None
    field, _, name = what.partition(":")
    key = {"spend_cap": "max", "save": "min", "extra_payment": "extra", "plan_change": "planned"}.get(kind)
    t = {field: name}
    if amount:
        try:
            t[key] = float(amount.replace(",", "").replace("$", ""))
        except ValueError:
            raise ValueError("the amount is a number of dollars") from None
    if form.get("until_payoff") and kind == "extra_payment":
        t["until_payoff"] = True
    return t


@router.post("/checkin/{month}/decisions")
async def add_decision(month: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    form = await request.form()
    with db.pool.connection() as conn:
        try:
            new_id, n = decisions.add(conn, month, str(form.get("text") or ""), str(form.get("kind") or "other"), _target(form),
                                 str(form.get("until") or "") or None, str(form.get("person") or "") or None, str(form.get("note") or ""), _who(user))
            conn.commit()
        except ValueError as e:
            conn.rollback(); field = _field_of(str(e))
            if form.get("from") == "form":   # the typed values come back, with the error under its field (DS-FORM-1)
                request.session["dec_form"] = {k: str(form.get(k) or "") for k in ("text", "kind", "what", "amount", "until", "until_payoff", "person")} | {
                    "month": month, "field": field, "error": str(e)}
            else:
                flash(request, f"Not added: {e}.", "bad")
            return _back(month, "next", f"dec-{field}" if field and form.get("from") == "form" else "dec-text")
    msg = f"Added “{str(form.get('text') or '').strip()}” for {month_text(decisions.rules.add_months(month, 1))}."
    if n > decisions.rules.SOFT_CAP:
        msg += f" That is {n} this month: three is usually enough to act on."
    flash(request, msg)
    # from the form: back to its first field, to add another; from a suggestion (which may now be gone): the new decision's row
    return _back(month, "next", "dec-text" if form.get("from") == "form" else f"dec-remove-{new_id}")


@router.post("/checkin/{month}/decisions/{decision_id}/verdict")
async def confirm_decision(month: str, decision_id: int, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    form = await request.form()
    with db.pool.connection() as conn:
        try:
            decisions.confirm(conn, decision_id, month, str(form.get("verdict") or ""), _who(user)); conn.commit()
            row = conn.execute("SELECT text FROM decisions WHERE id = %s", (decision_id,)).fetchone()
            flash(request, f"Marked “{row['text'] if row else 'the decision'}” as {decisions.VERDICT_WORDS.get(str(form.get('verdict')), 'confirmed')}.")   # DS-FLASH-1
        except ValueError as e:
            conn.rollback(); flash(request, f"Not saved: {e}.", "bad")
    v = str(form.get("verdict") or "")
    return _back(month, "decisions", f"verdict-{decision_id}-{v}" if v in ("kept", "partly", "dropped") else f"decision-{decision_id}")


@router.post("/checkin/{month}/decisions/{decision_id}/carry")
def carry_decision(month: str, decision_id: int, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        try:
            nxt = decisions.carry_on(conn, decision_id, month); conn.commit()
            flash(request, f"Carried on: it is checked again at the {month_text(nxt)} check-in.")
        except ValueError as e:
            conn.rollback(); flash(request, f"Not carried on: {e}.", "bad")
    return _back(month, "decisions", f"decision-{decision_id}")   # the carry button is gone once it is carried: the row itself


@router.post("/checkin/{month}/decisions/{decision_id}/delete")
async def delete_decision(month: str, decision_id: int, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    form = await request.form()
    with db.pool.connection() as conn:
        row = conn.execute("SELECT text FROM decisions WHERE id = %s", (decision_id,)).fetchone()
        decisions.delete(conn, decision_id); conn.commit()
    flash(request, f"Removed “{row['text']}”." if row else "Removed.")
    return _back(month, str(form.get("step") or "next"), str(form.get("focus") or "") or None)   # the neighbouring row, named by the page
