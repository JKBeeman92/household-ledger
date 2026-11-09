"""Fetch the latest CPI (appends missing months, replaces estimated ones) and choose the series URL."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth, cpi_fetch
from ..web_helpers import flash, render
from pipeline.run_pipeline import rebuild

router = APIRouter()


@router.get("/cpi", response_class=HTMLResponse)
def cpi_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        url = cpi_fetch.series_url(conn)
        rows = conn.execute("SELECT month, cpi, source FROM cpi ORDER BY month DESC").fetchall()
    est = [r for r in rows if cpi_fetch.is_estimate(r["source"])]
    return render(request, "cpi.html", user, active="settings", url=url, default_url=cpi_fetch.DEFAULT_URL, latest=rows[0] if rows else None, estimated=est)


@router.post("/cpi/url")
def cpi_url_save(request: Request, url: str = Form(""), user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        try:
            url = url.strip()
            if url and url != cpi_fetch.DEFAULT_URL:
                db.put_setting(conn, cpi_fetch.SETTING, cpi_fetch.check_url(url))
            else:
                conn.execute("DELETE FROM settings WHERE key=%s", (cpi_fetch.SETTING,))
            conn.commit(); flash(request, "Saved the CPI series URL.")
        except cpi_fetch.CpiFetchError as e:
            conn.rollback(); flash(request, f"Not saved: {e}", "bad")
    return RedirectResponse("/cpi", status_code=303)


@router.post("/cpi/fetch")
def cpi_fetch_now(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        try:
            res = cpi_fetch.fetch_latest(conn); conn.commit()
        except cpi_fetch.CpiFetchError as e:
            conn.rollback(); flash(request, f"Nothing changed: {e}", "bad")
            return RedirectResponse("/cpi", status_code=303)
        n = len(res["added"]) + len(res["replaced"])
        if not n:
            flash(request, "CPI is already current: nothing to add or replace.")
            return RedirectResponse("/cpi", status_code=303)
        msg = f"Added {len(res['added'])} month(s), replaced {len(res['replaced'])} estimated month(s)."
        try:
            r = rebuild(conn)
            flash(request, f"{msg} Rebuilt: {r['summary']}.", "ok" if r["reconciliation"]["clean"] else "bad")
        except Exception as e:  # noqa: BLE001
            flash(request, f"{msg} Rebuild failed: {str(e)[:800]}", "bad")
    return RedirectResponse("/cpi", status_code=303)
