"""OIDC authorization-code login against any OpenID Connect provider, with a group check that is repeated while the session lives.

The app is its own OIDC client (no authenticating proxy in front): the provider issues an ID token, we verify it via authlib's
discovery-backed client, read the `groups` claim and admit only members of ALLOWED_GROUPS.

A sign-in has two halves. The signed session cookie (4 KB, shared with flash messages and the CSRF token) carries only a
random session id and the small user dict. The tokens, which do not fit (an id_token alone is often 1-2 KB), live in
`auth_sessions` (migration 0010) under the sha256 of that id: the id_token for logout's `id_token_hint`, and the access and
refresh tokens for the re-check, plus the groups from the last sign-in or re-check, which alone decide the role (the cookie's
copy is for display; an older, still validly signed copy of the cookie must not bring back an old role). `revalidate` (an app-level dependency) ends the session when its row is gone (signed out
elsewhere), past SESSION_MAX_AGE since sign-in, or when the GROUP_RECHECK_SECONDS re-check fails: every interval one request
refreshes the token and reads `groups` from userinfo again, so removing someone from the group, disabling them or revoking
the grant at the IdP locks them out within that interval. Any failure of the re-check, IdP unreachable included, ends the
session (fail closed); the next page load goes back through sign-in.
"""
from __future__ import annotations
import hashlib
import hmac
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
SCOPE = "openid email profile groups offline_access"   # offline_access: many providers (Authentik among them) issue a refresh token only for it


def make_oauth(s=settings, **client_kwargs) -> OAuth:
    """The OIDC client registry; tests pass an httpx `transport` that routes to an in-process IdP."""
    reg = OAuth()
    if s.oidc_issuer:
        reg.register(name="idp", server_metadata_url=s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
                     client_id=s.oidc_client_id, client_secret=s.oidc_client_secret, client_kwargs={"scope": SCOPE, **client_kwargs})
    return reg


oauth = make_oauth()

DEV_USER = {"email": "dev@localhost", "name": "Local developer", "groups": list(settings.allowed_groups), "is_editor": True}


class Forbidden(HTTPException):
    """A signed-in viewer reached an editor route: main.py renders a 403 page (JSON for API callers) instead of redirecting."""
    def __init__(self, detail: str = "Your account can view the ledger but not change it.", headers: dict | None = None):
        super().__init__(status_code=403, detail=detail, headers=headers)


def is_editor(groups) -> bool:
    return bool(set(groups or []) & set(settings.editors))


# ---- API tokens ----
TOKEN_PREFIX = "hl_"
SCOPES = {"read": frozenset({"read"}), "read+review": frozenset({"read", "review"})}
REALM = 'Bearer realm="household-ledger"'
_UNSET = object()


def bearer(request: Request) -> str | None:
    """The token of an `Authorization: Bearer` header ("" when empty), or None when the request has no Bearer header."""
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h[:7].lower() == "bearer " or h.lower() == "bearer" else None


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """(token shown once, hash stored): 256 random bits."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def _lookup_token(raw: str) -> dict | None:
    if not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
        return None
    h = hash_token(raw)
    with db.pool.connection() as conn:
        row = conn.execute("SELECT id, token_hash, name, scope, owner_email, last_used_at < now() - interval '60 seconds' OR last_used_at IS NULL AS stale "
                           "FROM api_tokens WHERE token_hash=%s AND revoked_at IS NULL", (h,)).fetchone()
        if not row or not hmac.compare_digest(row["token_hash"].encode(), h.encode()):
            return None
        if row["stale"]:   # last-used time to the minute, without a write on every request
            conn.execute("UPDATE api_tokens SET last_used_at=now() WHERE id=%s", (row["id"],))
    return row


def token_user(request: Request) -> dict | None:
    """The user a valid Bearer token stands for (cached on the request), or None for no Bearer header or an unknown/revoked token."""
    state = request.scope.setdefault("state", {})
    if (hit := state.get("api_token_user", _UNSET)) is not _UNSET:
        return hit
    raw = bearer(request)
    row = _lookup_token(raw) if raw else None
    user = row and {"email": row["owner_email"], "name": f"API token {row['name']}", "groups": [], "is_editor": False,
                    "token": {"id": row["id"], "name": row["name"], "scope": row["scope"]}}
    state["api_token_user"] = user or None
    return user or None


def revoke_tokens_of(email: str) -> int:
    with db.pool.connection() as conn:
        return conn.execute("UPDATE api_tokens SET revoked_at=now() WHERE owner_email=%s AND revoked_at IS NULL", (email,)).rowcount


def current_user(request: Request) -> dict | None:
    """The signed-in user with `is_editor` computed from the groups on the server-side row, which `revalidate` loads for this
    request (refreshed at every group re-check). Before that has run, or while a re-check is pending, nobody is an editor.
    None for any request with a Bearer header: a token never stands in for a session (HTML routes send it to sign-in)."""
    if bearer(request) is not None:
        return None
    if settings.auth_disabled:
        return DEV_USER
    u = request.session.get("user") if request.session.get("sid") else None
    return {**u, "is_editor": is_editor(getattr(request.state, "auth_groups", None))} if u else None


def require_user(request: Request) -> dict:
    """Dependency for HTML routes: redirect to login when anonymous. An unsafe method gets 303, so the browser GETs the login
    page instead of re-sending the form (307 keeps method and body, and /login is GET-only)."""
    user = current_user(request)
    if user is None:
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        status = 307 if request.method in ("GET", "HEAD") else 303
        raise HTTPException(status_code=status, headers={"Location": f"/login?next={quote(nxt, safe='/')}"})
    return user


def require_user_api(request: Request) -> dict:
    """Dependency for read-only JSON routes: a session user or a valid API token; 401 (with WWW-Authenticate) otherwise."""
    if bearer(request) is not None:
        if (user := token_user(request)) is None:
            raise HTTPException(status_code=401, detail="invalid or revoked API token", headers={"WWW-Authenticate": REALM + ', error="invalid_token"'})
        return user
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required", headers={"WWW-Authenticate": REALM})
    return user


def require_scope(need: str):
    """Dependency factory for JSON routes a token may use: `read` (anyone signed in, any token) or `review` (an editor's
    session, or a `read+review` token). Not for uploads, settings or rebuilds: those use require_editor(_api), which no token passes."""
    assert need in ("read", "review")

    def dep(request: Request) -> dict:
        user = require_user_api(request)
        tok = user.get("token")
        if tok and need not in SCOPES[tok["scope"]]:
            raise Forbidden(f"this token's scope ({tok['scope']}) does not include {need}", headers={"WWW-Authenticate": REALM + ', error="insufficient_scope"'})
        if not tok and need == "review" and not user["is_editor"]:
            raise Forbidden()
        return user
    return dep


def require_editor(request: Request) -> dict:
    """Dependency for HTML routes that change state: anonymous goes to sign-in, a viewer gets the 403 page."""
    user = require_user(request)
    if not user["is_editor"]:
        raise Forbidden()
    return user


def require_editor_api(request: Request) -> dict:
    """Dependency for JSON routes that change state. An API token never passes, whatever its scope."""
    user = require_user_api(request)
    if user.get("token"):
        raise Forbidden("API tokens cannot change settings, upload or rebuild; sign in instead.")
    if not user["is_editor"]:
        raise Forbidden()
    return user


def _safe_next(nxt: str | None) -> str:
    ok = nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and not any(ord(c) < 32 or ord(c) == 127 for c in nxt)
    return nxt if ok else "/"


# ---- server-side session rows ----
def _key(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _store(sid: str, sub: str, email: str, token: dict, groups: list[str]) -> None:
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < now()")
        conn.execute("INSERT INTO auth_sessions (id_hash, sub, email, id_token, access_token, access_expires_at, refresh_token, groups, expires_at) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))",
                     (_key(sid), sub, email, token.get("id_token"), token.get("access_token"), token.get("expires_at"), token.get("refresh_token"), groups, settings.session_max_age))


def _state(sid: str) -> dict | None:
    """None when there is no live row; else whether a group re-check is due (always, for a row without groups) and the groups."""
    with db.pool.connection() as conn:
        return conn.execute("SELECT checked_at <= now() - make_interval(secs => %s) OR groups IS NULL AS due, groups FROM auth_sessions WHERE id_hash=%s AND expires_at > now()",
                            (settings.group_recheck_seconds, _key(sid))).fetchone()


def _claim(sid: str) -> dict | None:
    """Atomically take the due re-check, so concurrent requests (the dashboard fetches several) refresh the token once.
    A row without groups gets '{}' (no role) while its re-check runs, so a concurrent request neither claims it again nor edits."""
    with db.pool.connection() as conn:
        return conn.execute("UPDATE auth_sessions SET checked_at=now(), groups=coalesce(groups, '{}') WHERE id_hash=%s "
                            "AND (checked_at <= now() - make_interval(secs => %s) OR groups IS NULL) "
                            "RETURNING sub, access_token, access_expires_at, refresh_token", (_key(sid), settings.group_recheck_seconds)).fetchone()


def _update(sid: str, token: dict, groups: list[str]) -> None:
    with db.pool.connection() as conn:
        conn.execute("UPDATE auth_sessions SET access_token=%s, access_expires_at=%s, refresh_token=%s, id_token=coalesce(%s, id_token), groups=%s WHERE id_hash=%s",
                     (token.get("access_token"), token.get("expires_at"), token.get("refresh_token"), token.get("id_token"), groups, _key(sid)))


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


async def _recheck(row: dict) -> tuple[dict, list[str]] | tuple[None, None]:
    """Ask the IdP again: refresh the token (when we have a refresh token) and read the current groups from userinfo."""
    token = {"access_token": row["access_token"], "token_type": "Bearer", "expires_at": row["access_expires_at"]}
    if row["refresh_token"]:
        md = await oauth.idp.load_server_metadata()
        async with oauth.idp._get_oauth_client(**md) as c:
            token = dict(await c.refresh_token(md["token_endpoint"], refresh_token=row["refresh_token"]))
    info = await oauth.idp.userinfo(token=token)
    if info.get("sub") != row["sub"] or not _admitted(info.get("groups")):
        return None, None
    return token, sorted(info.get("groups") or [])


async def revalidate(request: Request) -> None:
    """App-level dependency: end a session whose row is gone or expired, and re-check its groups every GROUP_RECHECK_SECONDS.
    A Bearer request is left alone: its session is ignored anyway (current_user), and the token is checked by require_user_api."""
    if settings.auth_disabled or bearer(request) is not None or not ("user" in request.session or "sid" in request.session):
        return
    sid = request.session.get("sid")
    st = sid and await run_in_threadpool(_state, sid)
    if not st:
        _end(request)
        return
    request.state.auth_groups = st["groups"]
    if not st["due"] or not (row := await run_in_threadpool(_claim, sid)):
        return
    email = request.session.get("user", {}).get("email")
    try:
        token, groups = await _recheck(row)
        denied = token is None
    except Exception as e:  # noqa: BLE001 - any failure fails closed
        log.warning("group re-check failed for %s: %s: %s", email, type(e).__name__, e)
        token = groups = None
        denied = False   # IdP unreachable is not a verdict on the person: their API tokens stay
    if email and (denied or (token and not is_editor(groups))) and (n := await run_in_threadpool(revoke_tokens_of, email)):
        log.info("revoked %d API token(s) of %s: no longer an editor", n, email)
    if token:
        await run_in_threadpool(_update, sid, token, groups)
        request.state.auth_groups = groups                                       # a role change at the IdP applies within the interval
        request.session["user"] = {**request.session["user"], "groups": groups}   # display copy only
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
    await run_in_threadpool(_store, sid, claims.get("sub", ""), claims.get("email", ""), token, sorted(groups))
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
