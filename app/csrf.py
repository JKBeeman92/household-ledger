"""CSRF protection: a per-session token, enforced in ONE app-level dependency on every unsafe request.

The token lives in the signed session cookie. Forms render it with `{{ csrf_field(request) }}`; scripts send it as the
`X-CSRF-Token` header. Routes need do nothing to be covered (the dependency is registered on the app), so a new POST route
cannot forget it. A request authenticated by a valid API token is exempt through `csrf_exempt` (#34)."""
from __future__ import annotations
import hmac
import secrets

from fastapi import Request
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from . import auth

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
FIELD = "csrf_token"
HEADER = "X-CSRF-Token"


class CsrfError(Exception):
    pass


def token_for(request: Request) -> str:
    """The session's token, minted on first use."""
    tok = request.session.get(FIELD)
    if not tok:
        tok = request.session[FIELD] = secrets.token_urlsafe(32)
    return tok


def csrf_field(request: Request) -> Markup:
    return Markup(f'<input type="hidden" name="{FIELD}" value="{token_for(request)}">')


def csrf_exempt(request: Request) -> bool:
    """True only when the request's `Authorization: Bearer` token validates. Such a request is judged by the token alone (its session
    cookie is ignored, app/auth.py), so there is no ambient authority to forge. An unknown, revoked or empty token is not exempt.
    Runs in a worker thread (one indexed lookup, cached on the request for the auth dependency)."""
    return auth.bearer(request) is not None and auth.token_user(request) is not None


async def protect(request: Request) -> None:
    """App-level dependency: raise CsrfError on an unsafe request without the session's token."""
    if request.method in SAFE_METHODS or await run_in_threadpool(csrf_exempt, request):
        return
    want = request.session.get(FIELD)
    got = request.headers.get(HEADER)
    if not got and request.headers.get("content-type", "").split(";")[0].strip() in ("application/x-www-form-urlencoded", "multipart/form-data"):
        got = (await request.form()).get(FIELD)   # Starlette caches the parsed form, so the route still reads it
    if not want or not isinstance(got, str) or not hmac.compare_digest(want.encode(), got.encode()):
        raise CsrfError
