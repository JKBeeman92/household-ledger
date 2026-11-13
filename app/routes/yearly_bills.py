"""Settings → Yearly bills (#151): confirm or correct a detected bill, add one, dismiss one, or restore one to the list the app
finds. Each action returns to the row it was made on (DS-FLASH-1, DS-A11Y-5) and asks for a rebuild, which applies it. A failed add
keeps what was typed and says which field to fix (DS-FORM-1)."""
from __future__ import annotations
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import auth, db, yearly_bills
from ..web_helpers import flash
from .checkin import _who

router = APIRouter()


def slug(item: str) -> str:
    """A stable anchor for a bill's row: 'yb-' and the item, lower case, letters and digits joined by hyphens."""
    return "yb-" + (re.sub(r"[^a-z0-9]+", "-", (item or "").lower()).strip("-") or "item")


def _back(focus: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?focus={quote(focus)}#yearly-bills", status_code=303)


def _neighbour(conn, item: str) -> str:
    """The row to land on after this one leaves the list: the next live bill, else the one before, else the dismissed count."""
    live = [b["item"] for b in yearly_bills.for_page(conn) if b["source"] != "dismissed"]
    if item in live and len(live) > 1:
        i = live.index(item)
        return slug(live[i + 1] if i + 1 < len(live) else live[i - 1])
    return "yb-dismissed"


@router.post("/yearly-bills")
async def save_bill(request: Request, user=Depends(auth.require_editor)):
    f = await request.form()
    item, source = str(f.get("item") or "").strip(), str(f.get("source") or "confirmed")
    with db.pool.connection() as conn:
        try:
            yearly_bills.save(conn, item, f.get("month"), f.get("amount"), f.get("every_years") or 1, str(f.get("funded_by") or ""), source, _who(user))
            conn.commit()
            flash(request, f"Saved {item} as a yearly bill. Rebuild to see when it falls due and what is set aside.")
            return _back(slug(item))
        except yearly_bills.BillError as e:
            conn.rollback()
            # the form comes back open where it was sent from (the add form, or the row's Change), with what was typed and the error
            # under the field to fix (DS-FORM-1); a Confirm opens its row's Change with the values the app found
            row = None if f.get("form") == "add" else slug(item)
            request.session["yb_form"] = {k: str(f.get(k) or "") for k in ("item", "month", "amount", "every_years", "funded_by")} | {
                "field": e.field, "error": str(e), "row": row}
            return _back(f"{row}-change" if row else "yb-add")


@router.post("/yearly-bills/{item}/dismiss")
def dismiss_bill(item: str, request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        focus = _neighbour(conn, item)
        try:
            yearly_bills.dismiss(conn, item, _who(user)); conn.commit()
            flash(request, f"Dismissed {item}: it stays off the list across rebuilds. Restore it from the dismissed list.")
        except yearly_bills.BillError as e:
            conn.rollback(); flash(request, f"Not dismissed: {e}", "bad"); focus = slug(item)
    return _back(focus)


@router.post("/yearly-bills/{item}/restore")
def restore_bill(item: str, request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        yearly_bills.restore(conn, item); conn.commit()
    flash(request, f"Restored {item}: rebuild to see whether the app finds it as a yearly bill.")
    return _back("yb-dismissed")
