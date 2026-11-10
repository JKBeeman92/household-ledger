"""API tokens (#34): created once on their own page, stored hashed, scoped, revocable, accepted only on token-capable JSON routes."""
from __future__ import annotations
import dataclasses, hashlib, re

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, csrf
from conftest import make_client
from test_oidc import RECHECK, age_check, browser, idp, roles, sign_in, sql  # noqa: F401  (fixtures)
from test_routes import auth_on  # noqa: F401  (fixture)


def create(c, name="Bag End monitoring", scope="read"):
    r = c.post("/settings/tokens/new", data={"name": name, "scope": scope})
    assert r.status_code == 200, r.text
    return r, re.search(r'value="(hl_[^"]+)"', r.text).group(1)


def make_token(scope="read", owner="dev@localhost", name="Gaffer's cron") -> str:
    raw, digest = auth.new_token()
    sql("INSERT INTO api_tokens (token_hash, name, scope, owner_email) VALUES (%s, %s, %s, %s)", digest, name, scope, owner)
    return raw


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_created_on_its_own_page_shown_once_and_stored_only_as_a_hash(client, reset_db):
    page = client.get("/settings/tokens")
    assert page.status_code == 200 and 'action="/settings/tokens/new"' in page.text and 'name="csrf_token"' in page.text
    assert 'href="/settings/tokens"' in client.get("/settings").text
    r, tok = create(client, scope="read+review")
    assert r.headers["cache-control"] == "no-store" and len(tok) >= 40 and "Copy it now" in r.text
    rows = sql("SELECT * FROM api_tokens")
    assert len(rows) == 1 and rows[0]["token_hash"] == hashlib.sha256(tok.encode()).hexdigest()
    assert rows[0]["scope"] == "read+review" and rows[0]["owner_email"] == "dev@localhost" and rows[0]["last_used_at"] is None
    assert not any(tok in str(v) or tok[3:13] in str(v) for v in rows[0].values())
    assert tok not in client.get("/settings/tokens").text and tok not in client.cookies.get("session", "")
    assert "Bag End monitoring" in client.get("/settings/tokens").text and "never" in client.get("/settings/tokens").text
    assert create(client)[1] != tok


@pytest.mark.parametrize("data", [{"name": "", "scope": "read"}, {"name": "x", "scope": "admin"}, {"name": "x", "scope": "read+write"}])
def test_bad_create_is_refused(client, reset_db, data):
    r = client.post("/settings/tokens/new", data=data)
    assert r.status_code == 303 and sql("SELECT count(*) n FROM api_tokens")[0]["n"] == 0


def test_token_reads_api_and_metrics_and_records_last_use(client, reset_db, auth_on):
    tok = make_token()
    assert client.get("/api/status").status_code == 401
    for path in ("/api/status", "/api/datasets", "/metrics", "/api/build"):
        assert client.get(path, headers=bearer(tok)).status_code == 200, path
    used = sql("SELECT last_used_at FROM api_tokens")[0]["last_used_at"]
    assert used is not None
    client.get("/metrics", headers=bearer(tok))
    assert sql("SELECT last_used_at FROM api_tokens")[0]["last_used_at"] == used   # written at most once a minute
    sql("UPDATE api_tokens SET last_used_at = now() - interval '2 minutes'")
    client.get("/metrics", headers=bearer(tok))
    assert sql("SELECT last_used_at > now() - interval '10 seconds' AS fresh FROM api_tokens")[0]["fresh"]


def test_unknown_revoked_or_malformed_token_is_401_with_www_authenticate(client, reset_db):
    tok = make_token()
    sql("UPDATE api_tokens SET revoked_at = now()")
    for h in (f"Bearer {tok}", "Bearer hl_" + "A" * 43, "Bearer " + tok[3:], "Bearer ", "bearer x" * 50):
        r = client.get("/api/status", headers={"Authorization": h})
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer") and 'error="invalid_token"' in r.headers["www-authenticate"], h
    assert client.get("/api/status").status_code == 200   # the dev user's session path is untouched without a Bearer header


def test_plain_401_also_names_the_bearer_scheme(client, reset_db, auth_on):
    r = client.get("/metrics")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer realm=")


def test_revoke_from_the_page_and_only_your_own(client, reset_db):
    _, mine = create(client, "Hobbiton script")
    theirs = make_token(owner="lobelia@shire.test", name="Sackville")
    my_id = sql("SELECT id FROM api_tokens WHERE owner_email='dev@localhost'")[0]["id"]
    their_id = sql("SELECT id FROM api_tokens WHERE owner_email<>'dev@localhost'")[0]["id"]
    assert "Sackville" not in client.get("/settings/tokens").text
    assert 'data-confirm="Revoke Hobbiton script?' in client.get("/settings/tokens").text
    assert client.post(f"/settings/tokens/{their_id}/revoke").status_code == 404
    assert client.get("/api/status", headers=bearer(theirs)).status_code == 200
    r = client.post(f"/settings/tokens/{my_id}/revoke")
    assert r.status_code == 303 and r.headers["location"] == "/settings/tokens"
    assert client.get("/api/status", headers=bearer(mine)).status_code == 401
    assert "Hobbiton script" not in client.get("/settings/tokens").text.split("Your tokens")[1]
    assert client.post(f"/settings/tokens/{my_id}/revoke").status_code == 404
    assert sql("SELECT count(*) n FROM api_tokens WHERE revoked_at IS NOT NULL")[0]["n"] == 1   # kept, marked revoked


def test_token_page_is_editor_only(roles, browser):
    sign_in(browser, roles, "rosie", "/")
    assert browser.get("/settings/tokens").status_code == 403
    assert browser.post("/settings/tokens/new", data={"name": "x", "scope": "read"}).status_code == 403
    assert sql("SELECT count(*) n FROM api_tokens")[0]["n"] == 0


def _calls(dep):
    for d in dep.dependencies:
        yield d.call
        yield from _calls(d)


SESSION_ONLY = {auth.require_user, auth.require_editor, auth.require_editor_api}


def session_routes():
    import importlib, pkgutil
    import app.routes as pkg
    routes = [r for m in pkgutil.iter_modules(pkg.__path__) for r in importlib.import_module(f"app.routes.{m.name}").router.routes]
    for r in routes:
        if isinstance(r, APIRoute) and SESSION_ONLY & set(_calls(r.dependant)):
            for m in sorted(r.methods - {"HEAD"}):
                yield m, re.sub(r"\{[^}]+\}", "1", r.path)


def test_every_session_route_is_found():
    found = set(session_routes())
    for must in [("POST", "/upload"), ("GET", "/upload"), ("POST", "/rebuild"), ("GET", "/settings"), ("POST", "/settings/1"), ("POST", "/settings/1/row"),
                 ("POST", "/settings/1/row/delete"), ("GET", "/settings/tokens"), ("POST", "/settings/tokens/new"), ("GET", "/"), ("POST", "/review/generate")]:
        assert must in found, must


@pytest.mark.parametrize("scope", ["read", "read+review"])
def test_a_token_never_authorises_a_page_upload_settings_or_rebuild(client, reset_db, scope):
    """Even with the dev user (AUTH_DISABLED) and a session cookie present, a Bearer request is judged by its token alone."""
    tok = make_token(scope)
    before = sql("SELECT key, value FROM settings ORDER BY key")
    for method, path in session_routes():
        r = client.request(method, path, headers={**bearer(tok), "X-CSRF-Token": "unused"})
        assert r.status_code in (307, 403), (method, path, r.status_code)
        assert r.status_code == 403 or r.headers["location"].startswith("/login"), (method, path)
        assert "set-cookie" not in r.headers, (method, path)
    r = client.post("/settings/mapping/row", headers=bearer(tok), data={"item": "Second breakfast", "canonical_item": "Food", "category": "Food", "flow": "spend"})
    assert r.status_code == 403 and "cannot change settings" in r.json()["detail"]
    assert sql("SELECT key, value FROM settings ORDER BY key") == before
    assert sql("SELECT count(*) n FROM build_log")[0]["n"] == 0 and sql("SELECT count(*) n FROM api_tokens")[0]["n"] == 1


def test_csrf_exempts_only_a_validating_token(client, reset_db):
    from starlette.requests import Request
    def req(h):
        return Request({"type": "http", "method": "POST", "path": "/x", "headers": [(b"authorization", h.encode())] if h else [], "session": {}})
    tok = make_token()
    assert csrf.csrf_exempt(req(f"Bearer {tok}"))
    for h in ("", "Bearer ", "Bearer hl_nope", f"Basic {tok}", f"Bearer {tok}x"):
        assert not csrf.csrf_exempt(req(h)), h
    sql("UPDATE api_tokens SET revoked_at = now()")
    assert not csrf.csrf_exempt(req(f"Bearer {tok}"))


def test_lookup_compares_hashes_in_constant_time(client, reset_db, monkeypatch):
    seen = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    tok = make_token()
    assert client.get("/api/status", headers=bearer(tok)).status_code == 200
    assert (hashlib.sha256(tok.encode()).hexdigest().encode(),) * 2 in seen


@pytest.fixture
def scoped(client):
    app = FastAPI()
    @app.get("/read")
    def r(user=Depends(auth.require_scope("read"))): return {"email": user["email"]}
    @app.get("/review")
    def v(user=Depends(auth.require_scope("review"))): return {"email": user["email"]}
    from app.main import forbidden
    app.add_exception_handler(auth.Forbidden, forbidden)
    return TestClient(app)


def test_scope_dependency(scoped, reset_db, monkeypatch):
    read, both = make_token("read"), make_token("read+review")
    assert scoped.get("/read", headers=bearer(read)).json() == {"email": "dev@localhost"}
    r = scoped.get("/review", headers=bearer(read))
    assert r.status_code == 403 and 'error="insufficient_scope"' in r.headers["www-authenticate"]
    assert scoped.get("/review", headers=bearer(both)).status_code == 200 and scoped.get("/read", headers=bearer(both)).status_code == 200
    assert scoped.get("/review", headers=bearer("hl_unknown")).status_code == 401
    assert scoped.get("/review").status_code == 200          # dev user is an editor
    monkeypatch.setattr(auth, "DEV_USER", {**auth.DEV_USER, "is_editor": False})
    monkeypatch.setattr(auth, "is_editor", lambda groups: False)
    assert scoped.get("/read").status_code == 200 and scoped.get("/review", headers={"Accept": "application/json"}).status_code == 403   # a viewer's session reads, no more


def test_token_request_without_session_skips_the_oidc_recheck(idp, browser):
    tok = make_token(owner="frodo@shire.test")
    plain = make_client(csrf=False)
    r = plain.get("/api/status", headers=bearer(tok))
    assert r.status_code == 200 and "set-cookie" not in r.headers and idp.grants == []
    sign_in(browser, idp)                                     # a signed-in browser whose re-check is due
    age_check(RECHECK + 1)
    assert browser.get("/metrics", headers=bearer(tok)).status_code == 200
    assert idp.grants == ["authorization_code"]               # the Bearer request did not run the session's re-check
    assert browser.get("/metrics", headers=bearer("hl_nope")).status_code == 401   # nor did a bad token fall back to the session


def test_tokens_of_a_demoted_editor_are_revoked_at_the_recheck(roles, browser):
    sign_in(browser, roles, "frodo", "/")
    _, tok = create(browser)
    assert sql("SELECT owner_email FROM api_tokens")[0]["owner_email"] == "frodo@shire.test"
    roles.users["frodo"]["groups"] = ["family"]
    age_check(RECHECK + 1)
    browser.get("/api/status")
    assert make_client(csrf=False).get("/api/status", headers=bearer(tok)).status_code == 401


def test_tokens_survive_an_idp_outage_at_the_recheck(idp, browser):
    sign_in(browser, idp)
    _, tok = create(browser)
    idp.down = True
    age_check(RECHECK + 1)
    assert browser.get("/api/status").status_code == 401     # the session fails closed
    assert make_client(csrf=False).get("/api/status", headers=bearer(tok)).status_code == 200
