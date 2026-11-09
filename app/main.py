"""Household Ledger — FastAPI app. Routes live in app/routes/: auth, pages, upload (close a month), settings editors, api, brand."""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from . import db
from .web_helpers import ROOT
from .routes import brand, auth, pages, upload, settings as settings_routes, api

log = logging.getLogger("ledger")


@asynccontextmanager
async def lifespan(app: FastAPI):
    for p in settings.validate():
        log.warning("config: %s", p)
    db.init()
    yield
    db.pool.close()


app = FastAPI(title="Household Ledger", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or "dev-only-insecure-secret-change-me", https_only=settings.base_url.startswith("https"), same_site="lax", max_age=12 * 3600)
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
for module in (brand, auth, pages, upload, settings_routes, api):
    app.include_router(module.router)
