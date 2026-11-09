"""OIDC authorization-code login against Authentik, with a group check that is repeated while the session lives.

The app is its own OIDC client (no proxy outpost in front): Authentik issues an ID token, we verify it via authlib's
discovery-backed client, read the `groups` claim and admit only members of ALLOWED_GROUPS.

A sign-in has two halves. The signed session cookie (4 KB, shared with flash messages and the CSRF token) carries only a
random session id and the small user dict. The tokens, which do not fit (an Authentik id_token alone is 1-2 KB), live in
`auth_sessions` (migration 0010) under the sha256 of that id: the id_token for logout's `id_token_hint`, and the access and
refresh tokens for the re-check. `revalidate` (an app-level dependency) ends the session when its row is gone (signed out
elsewhere), past SESSION_MAX_AGE since sign-in, or when the GROUP_RECHECK_SECONDS re-check fails: every interval one request
refreshes the token and reads `groups` from userinfo again, so removing someone from the group, disabling them or revoking
the grant at the IdP locks them out within that interval. Any failure of the re-check, IdP unreachable included, ends the
session (fail closed); the next page load goes back through sign-in.
"""
from __future__ import annotations
import hashlib
import logging
import secrets
from urllib.parse import quote, urlencode
from fastapi import Request, HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth

from .config import settings
from . import db

log = logging.getLogger("ledger")
SCOPE = "openid email profile groups offline_access"   # offline_access: Authentik issues a refresh token only for it


def make_oauth(s=settings, **client_kwargs) -> OAuth:
    """The OIDC client registry; tests pass an httpx `transport` that routes to an in-process IdP."""
    reg = OAuth()
    if s.oidc_issuer:
        reg.register(name="idp", server_metadata_url=s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
                     client_id=s.oidc_client_id, client_secret=s.oidc_client_secret, client_kwargs={"scope": SCOPE, **client_kwargs})
    return reg


oauth = make_oauth()

DEV_USER = {"email": "dev@localhost", "name": "Local developer", "groups": list(settings.allowed_groups)}


def current_user(request: Request) -> dict | None:
    if settings.auth_disabled:
        return DEV_USER
    return request.session.get("user") if request.session.get("sid") else None


def require_user(request: Request) -> dict:
    """Dependency for HTML routes: redirect to login when anonymous."""
    user = current_user(request)
    if user is None:
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        raise HTTPException(status_code=307, headers={"Location": f"/login?next={quote(nxt, safe='/')}"})
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
    ok = nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and not any(ord(c) < 32 or ord(c) == 127 for c in nxt)
    return nxt if ok else "/"


# ---- server-side session rows ----
def _key(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _store(sid: str, sub: str, email: str, token: dict) -> None:
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < now()")
        conn.execute("INSERT INTO auth_sessions (id_hash, sub, email, id_token, access_token, access_expires_at, refresh_token, expires_at) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))",
                     (_key(sid), sub, email, token.get("id_token"), token.get("access_token"), token.get("expires_at"), token.get("refresh_token"), settings.session_max_age))


def _state(sid: str) -> dict | None:
    """None when there is no live row; else whether a group re-check is due."""
    with db.pool.connection() as conn:
        return conn.execute("SELECT checked_at <= now() - make_interval(secs => %s) AS due FROM auth_sessions WHERE id_hash=%s AND expires_at > now()",
                            (settings.group_recheck_seconds, _key(sid))).fetchone()


def _claim(sid: str) -> dict | None:
    """Atomically take the due re-check, so concurrent requests (the dashboard fetches several) refresh the token once."""
    with db.pool.connection() as conn:
        return conn.execute("UPDATE auth_sessions SET checked_at=now() WHERE id_hash=%s AND checked_at <= now() - make_interval(secs => %s) "
                            "RETURNING sub, access_token, access_expires_at, refresh_token", (_key(sid), settings.group_recheck_seconds)).fetchone()


def _update(sid: str, token: dict) -> None:
    with db.pool.connection() as conn:
        conn.execute("UPDATE auth_sessions SET access_token=%s, access_expires_at=%s, refresh_token=%s, id_token=coalesce(%s, id_token) WHERE id_hash=%s",
                     (token.get("access_token"), token.get("expires_at"), token.get("refresh_token"), token.get("id_token"), _key(sid)))


def _drop(sid: str | None) -> str | None:
    """Delete the row; return its id_token for the end-session hint."""
    if not sid:
        return None
    with db.pool.connection() as conn:
        row = conn.execute("DELETE FROM auth_sessions WHERE id_hash=%s RETURNING id_token", (_key(sid),)).fetchone()
    return row["id_token"] if row else None


def _end(request: Request) -> None:
    for k in ("user", "sid"):
        request.session.pop(k, None)


def _admitted(groups) -> bool:
    return bool(set(groups or []) & set(settings.allowed_groups))


async def _recheck(row: dict) -> dict | None:
    """Ask the IdP again: refresh the token (when we have a refresh token) and read the current groups from userinfo."""
    token = {"access_token": row["access_token"], "token_type": "Bearer", "expires_at": row["access_expires_at"]}
    if row["refresh_token"]:
        md = await oauth.idp.load_server_metadata()
        async with oauth.idp._get_oauth_client(**md) as c:
            token = dict(await c.refresh_token(md["token_endpoint"], refresh_token=row["refresh_token"]))
    info = await oauth.idp.userinfo(token=token)
    if info.get("sub") != row["sub"] or not _admitted(info.get("groups")):
        return None
    return token


async def revalidate(request: Request) -> None:
    """App-level dependency: end a session whose row is gone or expired, and re-check its groups every GROUP_RECHECK_SECONDS."""
    if settings.auth_disabled or not ("user" in request.session or "sid" in request.session):
        return
    sid = request.session.get("sid")
    st = sid and await run_in_threadpool(_state, sid)
    if not st:
        _end(request)
        return
    if not st["due"] or not (row := await run_in_threadpool(_claim, sid)):
        return
    try:
        token = await _recheck(row)
    except Exception as e:  # noqa: BLE001 - any failure fails closed
        log.warning("group re-check failed for %s: %s: %s", request.session.get("user", {}).get("email"), type(e).__name__, e)
        token = None
    if token:
        await run_in_threadpool(_update, sid, token)
    else:
        log.info("session ended by group re-check: %s", request.session.get("user", {}).get("email"))
        await run_in_threadpool(_drop, sid)
        _end(request)


async def start_login(request: Request, nxt: str | None) -> RedirectResponse:
    if settings.auth_disabled:
        return RedirectResponse(_safe_next(nxt))
    request.session["next"] = _safe_next(nxt)
    return await oauth.idp.authorize_redirect(request, settings.base_url + "/auth/callback")


async def finish_login(request: Request) -> RedirectResponse:
    token = await oauth.idp.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    if "groups" not in claims:
        claims = {**claims, **await oauth.idp.userinfo(token=token)}
    groups = set(claims.get("groups") or [])
    if not _admitted(groups):
        request.session.clear()
        raise HTTPException(status_code=403, detail=f"{claims.get('email', 'this account')} is not in an allowed group ({', '.join(settings.allowed_groups)})")
    await run_in_threadpool(_drop, request.session.get("sid"))
    sid = secrets.token_urlsafe(32)
    await run_in_threadpool(_store, sid, claims.get("sub", ""), claims.get("email", ""), token)
    request.session["sid"] = sid
    request.session["user"] = {"email": claims.get("email", ""), "name": claims.get("name") or claims.get("preferred_username", ""), "groups": sorted(groups)}
    nxt = request.session.pop("next", "/")
    return RedirectResponse(nxt, status_code=303)


async def logout(request: Request) -> str:
    """Drop the session (both halves) and return where to send the browser: the IdP's end-session endpoint with
    `id_token_hint` and `post_logout_redirect_uri` (so the IdP session ends too and the user lands back here), or home."""
    id_token = await run_in_threadpool(_drop, request.session.get("sid")) if not settings.auth_disabled else None
    request.session.clear()
    if settings.auth_disabled or not settings.oidc_issuer:
        return "/"
    try:
        end = (await oauth.idp.load_server_metadata()).get("end_session_endpoint")
    except Exception as e:  # noqa: BLE001
        log.warning("discovery failed at logout: %s", e)
        end = None
    end = end or settings.oidc_issuer.rstrip("/") + "/end-session/"
    params = {"client_id": settings.oidc_client_id, "post_logout_redirect_uri": settings.base_url + "/signed-out"}
    if id_token:
        params["id_token_hint"] = id_token
    return end + ("&" if "?" in end else "?") + urlencode(params)
