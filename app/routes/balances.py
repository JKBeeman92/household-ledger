"""Month-end balances at Close a month (issue #143): one field per account, pre-filled with the suggestion; the monthly check-in's
Balances step (#164) is this form. Backfill and accounts live on Settings → Accounts."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Depends
from fastapi.responses import RedirectResponse

from .. import db, auth
from ..web_helpers import flash, _months, month_text
from ..builds import run_background
from pipeline import accounts_store
from pipeline.run_pipeline import build_running

router = APIRouter()


def form_context(conn, month: str | None) -> dict:
    """What _balances.html draws: the month (the one asked for, else the latest loaded), the months to pick from, and its rows."""
    months = _months(conn)
    has_accounts = conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is not None
    if not (month and accounts_store.MONTH_RE.match(month)):
        month = months[0] if months else None
    rows = accounts_store.suggestions(conn, month) if month and has_accounts else []
    todo = [r["name"] for r in rows if r["stored"] is None]
    return {"month": month, "months": months, "rows": rows, "has_accounts": has_accounts, "to_do": todo}


def _who(user) -> str:
    return (user or {}).get("email") or (user or {}).get("name") or ""


@router.post("/balances/{month}")
async def save_balances(month: str, request: Request, background: BackgroundTasks, user=Depends(auth.require_editor)):
    form = await request.form()
    nxt = str(form.get("next") or "")   # the check-in's Balances step returns to it (#164)
    back = RedirectResponse(auth._safe_next(nxt) if nxt else f"/upload?balances_month={month}#balances", status_code=303)
    values = dict(zip((str(n) for n in form.getlist("account")), (str(v) for v in form.getlist("amount"))))
    request.session.pop("bal_form", None)
    with db.pool.connection() as conn:
        try:
            changed, errors = accounts_store.save_month(conn, month, values, _who(user))
        except ValueError as e:
            conn.rollback(); flash(request, f"Not saved: {e}", "bad")
            request.session["bal_form"] = {"month": month, "typed": values, "errors": {}, "error": str(e)}; return back
        if errors:
            conn.rollback()
            flash(request, "Not saved: " + "; ".join(f"{k}: {v}" for k, v in errors.items()), "bad")
            request.session["bal_form"] = {"month": month, "typed": values, "errors": errors, "error": ""}   # under each field too, with what was typed kept (DS-FORM-1)
            return back
        if changed:
            db.mark_inputs_changed(conn)
        conn.commit()
        left = accounts_store.month_status(conn, month)["to_do"]
        busy = build_running(conn)
    when = month_text(month)
    msg = (f"Saved {changed} balance{'s' * (changed != 1)} for the end of {when}." if changed else f"No balance changed for {when}.")
    msg += f" Still to confirm: {', '.join(left)}." if left else " Every account is confirmed."
    if changed and not busy:
        background.add_task(run_background); msg += " Rebuilding in the background."
    elif changed:
        msg += " A rebuild is running; press Rebuild when it finishes."
    flash(request, msg)   # a save that leaves accounts to confirm is still a good save; the sentence says what is left
    return back
