"""Start-up refuses invalid production config; auth redirects are encoded and `next` is safe."""
from __future__ import annotations
import dataclasses

import pytest

from app import auth
from app.config import ConfigError, Settings

GOOD = dict(auth_disabled=False, i_know_auth_is_off=False, in_container=False, session_secret="s" * 32, oidc_issuer="https://id.example.com/", oidc_client_id="ledger",
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


def test_unset_allowed_groups_refuses_to_start():
    """The default is no group at all, so a production config that never sets ALLOWED_GROUPS fails closed (issue #91)."""
    import os, subprocess, sys
    env = {k: v for k, v in os.environ.items() if k != "ALLOWED_GROUPS"}
    code = ("from app.config import Settings; s = Settings(auth_disabled=False, session_secret='s'*32, oidc_issuer='https://id.example.com/', "
            "oidc_client_id='ledger', oidc_client_secret='shh'); print(s.allowed_groups); print(s.validate())")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True, cwd=os.path.dirname(os.path.dirname(__file__))).stdout
    assert out.splitlines()[0] == "()"
    assert "ALLOWED_GROUPS must be set" in out


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


def test_auth_off_in_the_container_image_always_needs_the_override():
    """The image listens on 0.0.0.0, so a localhost BASE_URL says nothing about who can reach it."""
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    assert "LEDGER_IN_CONTAINER=1" in dockerfile and "0.0.0.0" in dockerfile
    with pytest.raises(ConfigError, match="I_KNOW_AUTH_IS_OFF"):
        cfg(auth_disabled=True, base_url="http://localhost:8010", in_container=True).enforce()
    cfg(auth_disabled=True, base_url="http://localhost:8010", in_container=True, i_know_auth_is_off=True).enforce()


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
    req = Request({"type": "http", "method": "GET", "path": "/x", "query_string": b"a=1&next=//evil", "headers": [], "session": {}})
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
