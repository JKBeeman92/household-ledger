"""Settings → Goals → Planned purchases (#158): add, change or delete a purchase saved for by a date; and, from the check-in's Where we
stand step or Settings, mark one bought (with a life event and, for a loan, the debt, each only when ticked). Each action returns to
the row it was made on (DS-FLASH-1, DS-A11Y-5) and asks for a rebuild; a failed save keeps what was typed and names the field (DS-FORM-1)."""
from __future__ import annotations
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import auth, db, purchases
from ..checkin import MONTH_RE
from ..web_helpers import flash
from .checkin import _who

router = APIRouter()
FIELDS = ("label", "amount", "month", "funded_by", "since", "note")
LOAN = ("loan_item", "loan_category", "loan_type", "loan_account", "loan_balance", "loan_apr")


def _back(focus: str, back: str = "") -> RedirectResponse:
    if MONTH_RE.match(back or ""):   # from the check-in: back to its Where we stand step
        return RedirectResponse(f"/checkin/{back}#{quote(focus)}", status_code=303)   # its hash opens the step and focuses the row
    return RedirectResponse(f"/settings?focus={quote(focus)}#goals/purchases", status_code=303)


@router.post("/purchases")
async def save_purchase(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    original = str(f.get("original") or "")
    with db.pool.connection() as conn:
        try:
            key = purchases.save(conn, str(f.get("label") or ""), f.get("amount"), str(f.get("month") or ""), str(f.get("funded_by") or ""),
                                 str(f.get("since") or ""), original, str(f["note"]).strip() if "note" in f else None)
            conn.commit()
        except purchases.PurchaseError as e:
            conn.rollback()
            flash(request, f"Not saved: {e}", "bad")
            request.session["pp_form"] = {k: str(f.get(k) or "") for k in FIELDS} | {"original": original, "field": e.field, "error": str(e)}
            return _back(purchases.anchor(original) if original else "pp-add")
    flash(request, f"Saved {str(f.get('label') or '').strip()}. Press Rebuild now to see what is saved so far and whether the pace gets there.")
    return _back(purchases.anchor(key))


@router.post("/purchases/{key}/delete")
def delete_purchase(key: str, request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        page = purchases.for_page(conn)
        rows, label = [p["key"] for p in page], next((p["label"] for p in page if p["key"] == key), "")
        n = purchases.delete(conn, key); conn.commit()
    flash(request, f"Deleted {label}." if n else "That planned purchase was already gone.")
    rest = [k for k in rows if k != key]
    if key in rows and rest:   # land on the neighbour, else the add form (DS-A11Y-5)
        i = rows.index(key)
        return _back(purchases.anchor(rest[min(i, len(rest) - 1)]))
    return _back("pp-add")


@router.post("/purchases/{key}/bought")
async def bought(key: str, request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    back = str(f.get("back") or "")
    names = {"loan_item": "item", "loan_category": "category", "loan_type": "debt_type", "loan_account": "account", "loan_balance": "balance", "loan_apr": "apr"}
    loan = {names[k]: str(f.get(k) or "") for k in LOAN} if f.get("with_loan") else None
    with db.pool.connection() as conn:
        try:
            done = purchases.mark_bought(conn, key, str(f.get("month") or ""), bool(f.get("add_event")), loan, _who(user))
            conn.commit()
        except purchases.PurchaseError as e:
            conn.rollback()
            flash(request, f"Not marked bought: {e}", "bad")
            request.session["pp_bought"] = {k: str(f.get(k) or "") for k in ("month", "add_event", "with_loan") + LOAN} | {"key": key, "field": e.field, "error": str(e)}
            return _back(purchases.anchor(key), back)
    extra = [x for x in ((f"added the life event {done['event']}" if done["event"] else None),
                         (f"set up {done['debt']} with the account {done['account']}" if done["debt"] else None)) if x]
    flash(request, "Recorded the purchase" + (": " + " and ".join(extra) if extra else "") + ". It leaves the check-in's list after Rebuild now.")
    return _back(purchases.anchor(key), back)


@router.post("/purchases/{key}/reopen")
async def reopen(key: str, request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    with db.pool.connection() as conn:
        purchases.reopen(conn, key); conn.commit()
    flash(request, "Open again: it is back in the check-in and coming up. A life event or a debt added when it was marked bought stays.")
    return _back(purchases.anchor(key), str(f.get("back") or ""))
