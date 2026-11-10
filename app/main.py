"""Household Ledger — FastAPI app. Routes live in app/routes/: auth, pages, upload (close a month), settings editors, api, brand."""
from __future__ import annotations
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .auth import bearer, current_user, revalidate, Forbidden
from .config import settings
from . import db, csrf, stale
from .security import SecurityHeaders
from .uploads import UploadLimit
from .web_helpers import ROOT, flash, render
from .logging_setup import configure as configure_logging, RequestIdMiddleware
from .routes import brand, auth, pages, upload, settings as settings_routes, api, build, findings, months, checklist, schemas, cpi, reviews, review_audit, review_prompt, ops, export, tokens, webhooks, sources
from .mcp_endpoint import endpoint as mcp_endpoint, PATH as MCP_PATH

configure_logging(settings.log_format)
log = logging.getLogger("ledger")


async def _stale_watch():
    """Announce missing budget months (webhook event months.missing) at start and every six hours; never raises."""
    while True:
        try:
            await asyncio.to_thread(stale.announce)
        except Exception:  # noqa: BLE001
            log.exception("stale-data check failed")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.enforce()  # raises ConfigError listing every problem; the server exits instead of starting
    if settings.auth_disabled:
        log.warning("AUTH_DISABLED=true: anyone who can reach this app is signed in as the dev user")
        if not settings.session_secret:
            log.warning("SESSION_SECRET is empty: using a random per-process secret, so sessions reset on restart")
    db.init()
    watch = asyncio.create_task(_stale_watch())
    async with mcp_endpoint.run():
        yield
    watch.cancel()
    db.pool.close()


app = FastAPI(title="Household Ledger", docs_url=None, redoc_url=None, lifespan=lifespan, dependencies=[Depends(csrf.protect), Depends(revalidate)])
app.add_middleware(UploadLimit, limit_bytes=lambda: settings.max_upload_mb * 2**20)   # 413 before a form parser spools an oversized body
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or secrets.token_hex(32), https_only=settings.base_url.startswith("https"), same_site="lax", max_age=settings.session_max_age)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeaders)   # CSP nonce + security headers on every response
app.add_middleware(RequestIdMiddleware)   # outermost, so every log line and response carries the request id
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
for module in (brand, auth, pages, upload, sources, settings_routes,   # sources before settings: its POST /settings/sources must win over /settings/{key}
                api, build, findings, months, checklist, schemas, cpi, reviews, review_audit, review_prompt, ops, export, tokens, webhooks):
    app.include_router(module.router)


app.add_route(MCP_PATH, mcp_endpoint)   # a bare ASGI route: bearer-token auth in the endpoint, not the app-level cookie/CSRF dependencies


@app.exception_handler(csrf.CsrfError)
async def csrf_rejected(request: Request, exc: csrf.CsrfError):
    msg = "Your form expired or could not be verified. Reload the page and try again."
    if "application/json" in request.headers.get("accept", "") or csrf.HEADER in request.headers:
        return JSONResponse({"detail": msg}, status_code=403)
    flash(request, msg, "bad")
    resp = render(request, "csrf_error.html", current_user(request))
    resp.status_code = 403
    return resp


@app.exception_handler(Forbidden)
async def forbidden(request: Request, exc: Forbidden):
    if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "") or bearer(request) is not None:
        return JSONResponse({"detail": exc.detail}, status_code=403, headers=exc.headers)
    csrf.token_for(request)   # a viewer's session has no form to mint one, and a later POST should read as forbidden, not as a forged form
    resp = render(request, "forbidden.html", current_user(request), detail=exc.detail)
    resp.status_code = 403
    return resp
