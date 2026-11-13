"""Webhooks page (#35), at /webhooks because /settings/{key} belongs to the settings editors: destination, delivery log, test button. The secret is write-only."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, auth, hooks
from ..config import settings
from ..web_helpers import flash, render

router = APIRouter()


@router.get("/webhooks", response_class=HTMLResponse)
def webhooks_page(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        cfg = hooks.config(conn)
        log = conn.execute("SELECT created_at, event, month, target, ok, http_status, error, duration_ms FROM webhook_deliveries ORDER BY id DESC LIMIT 50").fetchall()
    shown = {"url": cfg["url"], "enabled": cfg["enabled"], "secret_set": bool(cfg["secret"])}   # the secret itself never reaches a template
    return render(request, "webhooks.html", user, active="settings", hook_form=request.session.pop("hook_form", None), cfg=shown, log=log, events=hooks.EVENTS[:3], base_url=settings.base_url)


@router.post("/webhooks")
def webhooks_save(request: Request, url: str = Form(""), secret: str = Form(""), enabled: str = Form(""), user=Depends(auth.require_editor)):
    url, secret = url.strip(), secret.strip()
    with db.pool.connection() as conn:
        cur = hooks.config(conn)
        secret = secret or cur["secret"]   # blank keeps the stored secret
        if url and (why := hooks.check_url(url)):
            flash(request, "Not saved: see the URL below.", "bad")
            request.session["hook_form"] = {"url": url, "error": f"{why[:1].upper()}{why[1:]}."}   # kept, with the error under it (DS-FORM-1)
        elif enabled and not (url and secret):
            flash(request, "Not saved: enabling hooks needs a URL and a shared secret.", "bad")
        else:
            db.put_setting(conn, hooks.SETTING, {"url": url, "secret": secret if url else "", "enabled": bool(enabled and url)}); conn.commit()
            flash(request, "Saved." if url else "Webhook removed.")
    return RedirectResponse("/webhooks", status_code=303)


@router.post("/webhooks/test")
def webhooks_test(request: Request, user=Depends(auth.require_editor)):
    with db.pool.connection() as conn:
        cfg = hooks.config(conn)
        if not (cfg["url"] and cfg["secret"]):
            flash(request, "Save a URL and a shared secret first.", "bad")
        else:   # sent even while hooks are switched off, so a receiver can be set up before it is enabled; the person is waiting, so this one is synchronous
            res = hooks.deliver(conn, cfg, hooks.build_payload(conn, "test", note="A test event from the Webhooks page."))
            flash(request, "Test event delivered." if res["ok"] else f"Test failed: {res['error']}.", "ok" if res["ok"] else "bad")
    return RedirectResponse("/webhooks", status_code=303)
