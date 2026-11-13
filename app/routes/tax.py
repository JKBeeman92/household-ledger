"""Estimated tax (#153): Settings → Goals → Estimated tax, and a quarter's payment ticked at the check-in. Each returns where it was made."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import auth, db, tax
from ..web_helpers import flash, month_text
from .checkin import _month, _who

router = APIRouter()


def _names(text: str) -> list[str]:
    return [n.strip() for n in (text or "").replace("\n", ",").split(",") if n.strip()]


@router.post("/tax/settings")
async def tax_settings(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    request.session.pop("tax_form", None)
    with db.pool.connection() as conn:
        try:
            tax.save_settings(conn, _names(str(f.get("items") or "")), _names(str(f.get("aside") or "")), str(f.get("pct") or ""),
                              tax.parse_deadlines(str(f.get("deadlines") or "")))
            conn.commit()
            flash(request, "Saved the estimated-tax settings. Rebuild to see them at the check-in." if _names(str(f.get("items") or ""))
                  else "Estimated tax is off: no contract income items.")
        except tax.TaxError as e:   # shown under its field too, with what was typed kept (DS-FORM-1)
            conn.rollback(); flash(request, f"Not saved: {e}.", "bad")
            request.session["tax_form"] = {k: str(f.get(k) or "") for k in ("items", "aside", "pct", "deadlines")} | {"field": e.field, "error": str(e)}
    return RedirectResponse("/settings?focus=tax-settings#goals", status_code=303)


@router.post("/tax/{month}/paid")
async def tax_paid(month: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    f = await request.form()
    with db.pool.connection() as conn:
        try:
            tax.mark_paid(conn, month, f.get("amount"), _who(user)); conn.commit()
            flash(request, f"Marked the {month_text(month)} quarter's estimated tax paid.")
        except tax.TaxError as e:   # under the field at the step, with the typed amount kept (DS-FORM-1)
            conn.rollback(); flash(request, f"Not saved: {e}.", "bad")
            request.session["tax_paid_form"] = {"month": month, "amount": str(f.get("amount") or ""), "error": str(e)}
    return RedirectResponse(f"/checkin/{month}#step-tax", status_code=303)


@router.post("/tax/{month}/unpaid")
def tax_unpaid(month: str, request: Request, user=Depends(auth.require_editor)):
    month = _month(month)
    with db.pool.connection() as conn:
        tax.unmark(conn, month); conn.commit()
    flash(request, f"The {month_text(month)} quarter's estimated tax is no longer marked paid.")
    return RedirectResponse(f"/checkin/{month}#step-tax", status_code=303)
