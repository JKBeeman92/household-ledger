"""OIDC authorization-code login against Authentik, with a group check.

The app is its own OIDC client (no proxy outpost in front): Authentik issues an ID token, we verify it via authlib's
discovery-backed client, read the `groups` claim and admit only members of ALLOWED_GROUPS. Session is a signed cookie.
"""
from __future__ import annotations
from fastapi import Request, HTTPException
from starlette.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth

from .config import settings

oauth = OAuth()
if settings.oidc_issuer:
    oauth.register(
        name="idp",
        server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile groups"},
    )

DEV_USER = {"email": "dev@localhost", "name": "Local developer", "groups": list(settings.allowed_groups)}


def current_user(request: Request) -> dict | None:
    if settings.auth_disabled:
        return DEV_USER
    return request.session.get("user")


def require_user(request: Request) -> dict:
    """Dependency for HTML routes: redirect to login when anonymous."""
    user = current_user(request)
    if user is None:
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        raise HTTPException(status_code=307, headers={"Location": f"/login?next={nxt}"})
    return user


def require_user_api(request: Request) -> dict:
    """Dependency for JSON routes: 401 instead of a redirect (the page's shim handles the redirect)."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return user


def require_editor(request: Request) -> dict:
    """Dependency for HTML routes that change state. Every admitted user is an editor until roles exist."""
    return require_user(request)


def require_editor_api(request: Request) -> dict:
    """Dependency for JSON routes that change state."""
    return require_user_api(request)


def _safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


async def start_login(request: Request, nxt: str | None) -> RedirectResponse:
    if settings.auth_disabled:
        return RedirectResponse(_safe_next(nxt))
    request.session["next"] = _safe_next(nxt)
    return await oauth.idp.authorize_redirect(request, settings.base_url + "/auth/callback")


async def finish_login(request: Request) -> RedirectResponse:
    token = await oauth.idp.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    if not claims:
        claims = await oauth.idp.userinfo(token=token)
    groups = set(claims.get("groups") or [])
    if settings.allowed_groups and not (groups & set(settings.allowed_groups)):
        request.session.clear()
        raise HTTPException(status_code=403, detail=f"{claims.get('email', 'this account')} is not in an allowed group ({', '.join(settings.allowed_groups)})")
    request.session["user"] = {"email": claims.get("email", ""), "name": claims.get("name") or claims.get("preferred_username", ""), "groups": sorted(groups)}
    nxt = request.session.pop("next", "/")
    return RedirectResponse(nxt, status_code=303)


def logout_url() -> str:
    """Authentik's end-session endpoint for the application, when known; otherwise home."""
    if settings.oidc_issuer:
        return settings.oidc_issuer.rstrip("/") + "/end-session/"
    return "/"
