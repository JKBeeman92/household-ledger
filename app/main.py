"""Household Ledger — FastAPI app. Routes live in app/routes/: auth, pages, upload (close a month), settings editors, api, brand."""
from __future__ import annotations
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import current_user, revalidate, Forbidden
from .config import settings
from . import db, csrf
from .security import SecurityHeaders
from .web_helpers import ROOT, flash, render
from .routes import brand, auth, pages, upload, settings as settings_routes, api, build, findings, schemas, cpi, reviews, review_audit, review_prompt
from .routes import months, checklist, brand, auth, pages, upload, settings as settings_routes, api, schemas, cpi, reviews, review_audit, review_prompt

log = logging.getLogger("ledger")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.enforce()  # raises ConfigError listing every problem; the server exits instead of starting
    if settings.auth_disabled:
        log.warning("AUTH_DISABLED=true: anyone who can reach this app is signed in as the dev user")
        if not settings.session_secret:
            log.warning("SESSION_SECRET is empty: using a random per-process secret, so sessions reset on restart")
    db.init()
    yield
    db.pool.close()


app = FastAPI(title="Household Ledger", docs_url=None, redoc_url=None, lifespan=lifespan, dependencies=[Depends(csrf.protect), Depends(revalidate)])
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or secrets.token_hex(32), https_only=settings.base_url.startswith("https"), same_site="lax", max_age=settings.session_max_age)
app.add_middleware(SecurityHeaders)   # outermost: CSP nonce + security headers on every response
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
for module in (brand, auth, pages, upload, settings_routes, api, build, findings, months, checklist, schemas, cpi, reviews, review_audit, review_prompt):
    app.include_router(module.router)


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
    if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=403)
    csrf.token_for(request)   # a viewer's session has no form to mint one, and a later POST should read as forbidden, not as a forged form
    resp = render(request, "forbidden.html", current_user(request), detail=exc.detail)
    resp.status_code = 403
    return resp
