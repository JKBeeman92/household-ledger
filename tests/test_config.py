"""Start-up refuses invalid production config; auth redirects are encoded and `next` is safe."""
from __future__ import annotations
import dataclasses

import pytest

from app import auth
from app.config import ConfigError, Settings

GOOD = dict(auth_disabled=False, i_know_auth_is_off=False, session_secret="s" * 32, oidc_issuer="https://id.example.com/", oidc_client_id="ledger",
            oidc_client_secret="shh", allowed_groups=("admins",), base_url="https://ledger.example.com")


def cfg(**kw) -> Settings:
    return Settings(**{**GOOD, **kw})


def test_good_production_config_is_accepted():
    cfg().enforce()


@pytest.mark.parametrize("change, needle", [
    (dict(session_secret=""), "SESSION_SECRET"),
    (dict(session_secret="short"), "SESSION_SECRET"),
    (dict(oidc_issuer=""), "OIDC_ISSUER"),
    (dict(oidc_client_id=""), "OIDC_CLIENT_ID"),
    (dict(oidc_client_secret=""), "OIDC_CLIENT_SECRET"),
    (dict(allowed_groups=()), "ALLOWED_GROUPS"),
])
def test_bad_production_config_refuses_to_start(change, needle):
    with pytest.raises(ConfigError, match=needle):
        cfg(**change).enforce()


def test_every_problem_is_listed():
    with pytest.raises(ConfigError) as e:
        cfg(session_secret="", oidc_client_secret="", allowed_groups=()).enforce()
    assert all(n in str(e.value) for n in ("SESSION_SECRET", "OIDC_CLIENT_SECRET", "ALLOWED_GROUPS"))


def test_auth_off_needs_localhost_or_explicit_override():
    with pytest.raises(ConfigError, match="I_KNOW_AUTH_IS_OFF"):
        cfg(auth_disabled=True, base_url="https://ledger.example.com").enforce()
    cfg(auth_disabled=True, base_url="https://ledger.example.com", i_know_auth_is_off=True).enforce()
    for url in ("http://localhost:8010", "http://127.0.0.1:8010", "http://[::1]:8010"):
        cfg(auth_disabled=True, base_url=url, session_secret="").enforce()


def test_app_refuses_to_start_on_bad_config(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, auth_disabled=False, session_secret=""))
    with pytest.raises(ConfigError, match="SESSION_SECRET"), TestClient(main.app):
        pass


def test_no_hardcoded_session_secret():
    import inspect
    from app import main
    assert "insecure" not in inspect.getsource(main)


@pytest.mark.parametrize("bad", ["/\\evil.example", "/\\\\evil.example", "//evil.example", "/ok\nSet-Cookie: x=y", "/a\tb", "/a\x00b", "/a\x7f", "https://evil.example", ""])
def test_safe_next_rejects(bad):
    assert auth._safe_next(bad) == "/"


def test_safe_next_keeps_local_paths():
    assert auth._safe_next("/upload?month=2026-03") == "/upload?month=2026-03"


def test_login_redirect_encodes_next():
    from starlette.requests import Request
    from fastapi import HTTPException
    req = Request({"type": "http", "path": "/x", "query_string": b"a=1&next=//evil", "headers": [], "session": {}})
    with pytest.raises(HTTPException) as e:
        _anon(req)
    assert e.value.headers["Location"] == "/login?next=/x%3Fa%3D1%26next%3D//evil"


def _anon(req):
    from unittest import mock
    with mock.patch.object(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False)):
        return auth.require_user(req)


def test_error_redirect_is_encoded(client, monkeypatch):
    async def boom(request):
        raise auth.HTTPException(status_code=403, detail="a&next=//evil #x")
    monkeypatch.setattr(auth, "finish_login", boom)
    r = client.get("/auth/callback")
    assert r.headers["location"] == "/login?error=a%26next%3D%2F%2Fevil%20%23x"


def test_empty_allowed_groups_admits_nobody(client, monkeypatch):
    import asyncio
    from starlette.requests import Request

    class Idp:
        async def authorize_access_token(self, request): return {"userinfo": {"email": "pip@shire.test", "groups": ["hobbits"]}}
    monkeypatch.setattr(auth.oauth, "idp", Idp(), raising=False)
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False, allowed_groups=()))
    req = Request({"type": "http", "path": "/auth/callback", "query_string": b"", "headers": [], "session": {}})
    with pytest.raises(auth.HTTPException) as e:
        asyncio.run(auth.finish_login(req))
    assert e.value.status_code == 403 and "user" not in req.session
