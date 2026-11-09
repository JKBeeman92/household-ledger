"""CSRF protection: a per-session token, enforced in ONE app-level dependency on every unsafe request.

The token lives in the signed session cookie. Forms render it with `{{ csrf_field(request) }}`; scripts send it as the
`X-CSRF-Token` header. Routes need do nothing to be covered (the dependency is registered on the app), so a new POST route
cannot forget it. Requests that carry no ambient cookie authority are exempt through `csrf_exempt` (the seam for #34's API tokens)."""
from __future__ import annotations
import hmac
import secrets

from fastapi import Request
from markupsafe import Markup

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
    """True for requests authenticated by an `Authorization: Bearer` API token: they carry no cookie, so there is nothing to forge.
    Header presence is enough here because a cross-site page cannot set Authorization without a CORS preflight, which this app never grants;
    the token's own validity is checked by the API auth dependency (#34), not here."""
    return request.headers.get("authorization", "")[:7].lower() == "bearer "


async def protect(request: Request) -> None:
    """App-level dependency: raise CsrfError on an unsafe request without the session's token."""
    if request.method in SAFE_METHODS or csrf_exempt(request):
        return
    want = request.session.get(FIELD)
    got = request.headers.get(HEADER)
    if not got and request.headers.get("content-type", "").split(";")[0].strip() in ("application/x-www-form-urlencoded", "multipart/form-data"):
        got = (await request.form()).get(FIELD)   # Starlette caches the parsed form, so the route still reads it
    if not want or not isinstance(got, str) or not hmac.compare_digest(want.encode(), got.encode()):
        raise CsrfError
