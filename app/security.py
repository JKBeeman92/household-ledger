"""Security headers on every response (HTML, JSON, static), with a fresh CSP nonce per request.

Inline <script> blocks run only with `nonce="{{ csp_nonce(request) }}"`; scripts from /static are 'self'. style-src allows
'unsafe-inline' because the templates and the dashboard's tooltips use style attributes (so it must not carry a nonce, which
would switch 'unsafe-inline' off). Theme web fonts are the one external need: CSP_STYLE_SRC / CSP_FONT_SRC / CSP_IMG_SRC add
sources for a BRAND_DIR theme; the defaults cover Google Fonts, which the shipped theme @imports."""
from __future__ import annotations
import secrets

from fastapi import Request
from starlette.datastructures import MutableHeaders

from .config import settings


def csp(nonce: str, s=settings) -> str:
    return "; ".join(f"{d} {' '.join(v.split())}".strip() for d, v in (
        ("default-src", "'self'"),
        ("script-src", f"'self' 'nonce-{nonce}'"),
        ("style-src", f"'self' 'unsafe-inline' {s.csp_style_src}"),
        ("font-src", f"'self' data: {s.csp_font_src}"),
        ("img-src", f"'self' data: {s.csp_img_src}"),
        ("connect-src", "'self'"),
        ("object-src", "'none'"),
        ("base-uri", "'none'"),
        ("form-action", "'self'"),
        ("frame-ancestors", "'none'"),
    ))


def csp_nonce(request: Request) -> str:
    """Template global: this response's script nonce."""
    return request.scope.get("state", {}).get("csp_nonce", "")


class SecurityHeaders:
    """Pure ASGI middleware: puts the nonce in request.state before the route renders, adds the headers on the way out."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        nonce = secrets.token_urlsafe(18)
        scope.setdefault("state", {})["csp_nonce"] = nonce

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                for k, v in (("Content-Security-Policy", csp(nonce)), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "same-origin"),
                             ("X-Content-Type-Options", "nosniff")):
                    h.setdefault(k, v)
            await send(message)

        await self.app(scope, receive, send_with_headers)
