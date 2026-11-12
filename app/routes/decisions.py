"""Decisions (#155) at the check-in: add one for next month, confirm a month, carry one on, remove one. Every action returns to the
step it was made on (DS-FLASH-1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import auth, db, decisions
from ..checkin import MONTH_RE
from ..web_helpers import flash, month_text
from .checkin import _month, _who

router = APIRouter()


def _back(month: str, step: str) -> RedirectResponse:
    return RedirectResponse(f"/checkin/{month}#step-{step}", status_code=303)


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
            _, n = decisions.add(conn, month, str(form.get("text") or ""), str(form.get("kind") or "other"), _target(form),
                                 str(form.get("until") or "") or None, str(form.get("person") or "") or None, str(form.get("note") or ""), _who(user))
            conn.commit()
        except ValueError as e:
            conn.rollback(); flash(request, f"Not added: {e}.", "bad"); return _back(month, "next")
    msg = f"Added for {month_text(decisions.rules.add_months(month, 1))}."
    if n > decisions.rules.SOFT_CAP:
        msg += f" That is {n} this month: three is usually enough to act on."
    flash(request, msg)
    return _back(month, "next")


@router.post("/checkin/{month}/decisions/{decision_id}/verdict")
async def confirm_decision(month: str, decision_id: int, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    form = await request.form()
    with db.pool.connection() as conn:
        try:
            decisions.confirm(conn, decision_id, month, str(form.get("verdict") or ""), _who(user)); conn.commit()
        except ValueError as e:
            conn.rollback(); flash(request, f"Not saved: {e}.", "bad")
    return _back(month, "decisions")


@router.post("/checkin/{month}/decisions/{decision_id}/carry")
def carry_decision(month: str, decision_id: int, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        try:
            nxt = decisions.carry_on(conn, decision_id, month); conn.commit()
            flash(request, f"Carried on: it is checked again at the {month_text(nxt)} check-in.")
        except ValueError as e:
            conn.rollback(); flash(request, f"Not carried on: {e}.", "bad")
    return _back(month, "decisions")


@router.post("/checkin/{month}/decisions/{decision_id}/delete")
async def delete_decision(month: str, decision_id: int, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    form = await request.form()
    with db.pool.connection() as conn:
        decisions.delete(conn, decision_id); conn.commit()
    flash(request, "Removed.")
    return _back(month, str(form.get("step") or "next"))
