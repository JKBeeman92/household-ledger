"""Sign-in, logout and the group re-check against an in-process IdP (tests/fake_idp.py), plus the security headers."""
from __future__ import annotations
import dataclasses, re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2   # what authlib's client uses once it is installed (requirements.txt)
import pytest

from app import auth, security
from app.config import ConfigError, Settings
from conftest import make_client
from fake_idp import END_SESSION, ISSUER, FakeIdp

RECHECK = 300
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"


@pytest.fixture
def idp(client, reset_db, monkeypatch):
    fake = FakeIdp()
    s = dataclasses.replace(auth.settings, auth_disabled=False, oidc_issuer=ISSUER, oidc_client_id=fake.client_id, oidc_client_secret=fake.client_secret,
                            allowed_groups=("admins",), group_recheck_seconds=RECHECK, session_max_age=12 * 3600, base_url="http://testserver")
    monkeypatch.setattr(auth, "settings", s)
    monkeypatch.setattr(auth, "oauth", auth.make_oauth(s, transport=httpx2.ASGITransport(app=fake.app)))
    fake.add_user("frodo", "frodo@shire.test", ["admins", "hobbits"])
    fake.add_user("lobelia", "lobelia@shire.test", ["hobbits"])
    return fake


@pytest.fixture
def browser(idp):
    return make_client(csrf=True)   # own cookie jar; the app is already running under `client`


def sign_in(c, idp, sub="frodo", nxt="/upload"):
    r = c.get(f"/auth/start?next={nxt}")
    assert r.status_code == 302
    code, state = idp.authorize(r.headers["location"], sub)
    return c.get(f"/auth/callback?code={code}&state={state}")


def sql(q, *args):
    from app import db
    with db.pool.connection() as conn:
        cur = conn.execute(q, args)
        return cur.fetchall() if cur.description else None


def age_check(seconds):
    """Pretend the last group re-check happened `seconds` ago."""
    sql("UPDATE auth_sessions SET checked_at = now() - make_interval(secs => %s)", seconds)


def test_sign_in_keeps_tokens_server_side(idp, browser):
    r = sign_in(browser, idp)
    assert r.status_code == 303 and r.headers["location"] == "/upload"
    assert browser.get("/upload").status_code == 200
    rows = sql("SELECT sub, email, id_token, refresh_token FROM auth_sessions")
    assert len(rows) == 1 and rows[0]["sub"] == "frodo" and rows[0]["id_token"] == idp.id_tokens[-1] and rows[0]["refresh_token"]
    cookie = browser.cookies["session"]
    assert len(cookie) < 1024 and idp.id_tokens[-1] not in cookie


def test_non_member_is_refused(idp, browser):
    r = sign_in(browser, idp, "lobelia")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?error=") and "allowed%20group" in r.headers["location"]
    assert sql("SELECT count(*) n FROM auth_sessions")[0]["n"] == 0
    assert browser.get("/upload").status_code == 307


def test_logout_sends_id_token_hint_and_post_logout_redirect_uri(idp, browser):
    sign_in(browser, idp)
    stolen = browser.cookies["session"]
    r = browser.get("/logout")
    assert r.status_code == 303
    u = urlsplit(r.headers["location"])
    assert f"{u.scheme}://{u.netloc}{u.path}" == END_SESSION
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert q == {"id_token_hint": idp.id_tokens[-1], "post_logout_redirect_uri": "http://testserver/signed-out", "client_id": "ledger"}
    assert sql("SELECT count(*) n FROM auth_sessions")[0]["n"] == 0
    assert browser.get("/upload").status_code == 307
    assert "You are signed out" in browser.get("/signed-out").text
    browser.cookies.set("session", stolen)          # a copy of the cookie taken before logout is dead too
    assert browser.get("/api/status").status_code == 401


def test_member_stays_signed_in_and_refresh_token_rotates(idp, browser):
    sign_in(browser, idp)
    before = sql("SELECT refresh_token FROM auth_sessions")[0]["refresh_token"]
    age_check(RECHECK + 1)
    assert browser.get("/api/status").status_code == 200
    assert idp.grants == ["authorization_code", "refresh_token"]
    assert sql("SELECT refresh_token FROM auth_sessions")[0]["refresh_token"] != before
    assert browser.get("/api/status").status_code == 200 and idp.grants.count("refresh_token") == 1   # not due again yet


def test_removed_from_group_is_locked_out_within_the_interval(idp, browser):
    sign_in(browser, idp)
    idp.users["frodo"]["groups"] = ["hobbits"]
    age_check(RECHECK - 30)                                   # inside the interval: still the last verdict
    assert browser.get("/api/status").status_code == 200
    age_check(RECHECK)                                        # interval elapsed: the next request asks the IdP
    assert browser.get("/api/status").status_code == 401
    assert browser.get("/").headers["location"] == "/login?next=/"
    assert sql("SELECT count(*) n FROM auth_sessions")[0]["n"] == 0


@pytest.mark.parametrize("break_it", ["deactivated", "idp_down"])
def test_recheck_fails_closed(idp, browser, break_it):
    sign_in(browser, idp)
    if break_it == "deactivated":
        idp.users["frodo"]["active"] = False
    else:
        idp.down = True
    age_check(RECHECK + 1)
    assert browser.get("/api/status").status_code == 401


def test_session_max_age_is_absolute(idp, browser):
    sign_in(browser, idp)
    sql("UPDATE auth_sessions SET expires_at = now() - interval '1 second'")
    assert browser.get("/api/status").status_code == 401
    assert idp.grants == ["authorization_code"]


def test_cookie_without_server_row_is_anonymous(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))
    req = Request({"type": "http", "path": "/", "headers": [], "session": {"user": {"email": "pippin@shire.test"}}})
    assert auth.current_user(req) is None


# ---- security headers ----
@pytest.mark.parametrize("path", ["/", "/api/status", "/healthz", "/login", "/static/app.css", "/api/dataset/nope"])
def test_security_headers_on_every_response(client, reset_db, path):
    r = client.get(path)
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "same-origin" and r.headers["x-content-type-options"] == "nosniff"
    p = {d.split()[0]: d.split()[1:] for d in r.headers["content-security-policy"].split("; ")}
    assert p["frame-ancestors"] == ["'none'"] and p["default-src"] == ["'self'"] and "'unsafe-inline'" not in p["script-src"]
    assert "https://fonts.googleapis.com" in p["style-src"] and "'unsafe-inline'" in p["style-src"] and "https://fonts.gstatic.com" in p["font-src"]
    assert not any(s.startswith("'nonce-") for s in p["style-src"])     # a nonce would disable 'unsafe-inline' for style attributes


def test_every_inline_script_carries_this_responses_nonce(client, reset_db):
    pages = [client.get(p) for p in ("/", "/", "/upload", "/settings")]
    nonces = []
    for r in pages:
        nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
        tags = re.findall(r"<script\b[^>]*>", r.text)
        inline = [t for t in tags if "src=" not in t]
        assert inline and all(f'nonce="{nonce}"' in t for t in inline)
        nonces.append(nonce)
    assert len(set(nonces)) == len(nonces)


def test_templates_have_no_inline_script_without_nonce():
    for f in TEMPLATES.glob("*.html"):
        for tag in re.findall(r"<script\b[^>]*>", f.read_text()):
            assert "src=" in tag or 'nonce="{{ csp_nonce(request) }}"' in tag, f"{f.name}: {tag}"
        assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", f.read_text()), f"{f.name}: inline event handler (blocked by the CSP)"


def test_csp_extra_sources_are_configurable():
    s = dataclasses.replace(security.settings, csp_style_src="https://type.bywater.test", csp_font_src="", csp_img_src="https://img.bywater.test")
    p = {d.split()[0]: d.split()[1:] for d in security.csp("n0nce", s).split("; ")}
    assert p["style-src"] == ["'self'", "'unsafe-inline'", "https://type.bywater.test"] and p["font-src"] == ["'self'", "data:"]
    assert p["img-src"] == ["'self'", "data:", "https://img.bywater.test"] and p["script-src"] == ["'self'", "'nonce-n0nce'"]


@pytest.mark.parametrize("change, needle", [(dict(csp_font_src="https://a.test; script-src *"), "CSP_FONT_SRC"),
                                            (dict(group_recheck_seconds=0), "GROUP_RECHECK_SECONDS"),
                                            (dict(group_recheck_seconds=7200, session_max_age=3600), "GROUP_RECHECK_SECONDS")])
def test_bad_session_or_csp_config_refuses_to_start(change, needle):
    good = dict(auth_disabled=False, session_secret="s" * 32, oidc_issuer=ISSUER, oidc_client_id="ledger", oidc_client_secret="shh", allowed_groups=("admins",))
    with pytest.raises(ConfigError, match=needle):
        Settings(**{**good, **change}).enforce()


# ---- viewer vs editor (EDITOR_GROUPS) ----
@pytest.fixture
def roles(idp, monkeypatch):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, allowed_groups=("admins", "family"), editor_groups=("admins",)))
    idp.add_user("rosie", "rosie@shire.test", ["family"])
    return idp


def test_viewer_sees_the_dashboard_but_no_editor_nav(roles, browser):
    assert sign_in(browser, roles, "rosie", "/").headers["location"] == "/"
    page = browser.get("/")
    assert page.status_code == 200 and 'href="/upload"' not in page.text and 'href="/settings"' not in page.text
    assert browser.get("/api/status").status_code == 200
    assert 'href="/upload"' in sign_in_get(roles, "frodo", "/").text


def sign_in_get(idp, sub, nxt):
    c = make_client(csrf=True); sign_in(c, idp, sub, nxt); return c.get("/")


def test_viewer_gets_a_403_page_on_every_editor_route_and_nothing_changes(roles, browser):
    sign_in(browser, roles, "rosie", "/")
    for method, path, kw in [("get", "/upload", {}), ("get", "/settings", {}), ("post", "/upload", {"files": [("files", ("a.csv", b"a", "text/csv"))]}),
                             ("post", "/rebuild", {}), ("post", "/review/generate", {"data": {"month": "2026-09"}}),
                             ("post", "/settings/mapping", {"data": {"body": "x"}})]:
        r = getattr(browser, method)(path, **kw)
        assert r.status_code == 403 and "view this, not change it" in r.text, path
        assert "location" not in r.headers
    assert browser.post("/rebuild", headers={"accept": "application/json"}).json()["detail"]
    assert sql("SELECT count(*) n FROM build_log")[0]["n"] == 0


def test_editor_api_dependency_refuses_a_viewer_with_403_json():
    from starlette.requests import Request
    req = Request({"type": "http", "path": "/api/x", "query_string": b"", "headers": [], "session": {"sid": "s", "user": {"email": "r@shire.test", "groups": ["admins"]}},
                   "state": {"auth_groups": ["family"]}})   # the role is the server row's (loaded by revalidate), not the cookie's
    from unittest import mock
    with mock.patch.object(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False, allowed_groups=("admins", "family"), editor_groups=("admins",))):
        with pytest.raises(auth.Forbidden) as e:
            auth.require_editor_api(req)
        assert e.value.status_code == 403
        req.state.auth_groups = ["admins"]
        assert auth.require_editor_api(req)["is_editor"]


def test_editors_default_to_allowed_groups_and_dev_user_edits(idp, browser):
    assert auth.settings.editors == ("admins",)       # EDITOR_GROUPS unset
    sign_in(browser, idp)
    assert browser.get("/upload").status_code == 200
    assert auth.DEV_USER["is_editor"]


def test_role_change_applies_at_the_next_recheck(roles, browser):
    sign_in(browser, roles, "frodo", "/")
    assert browser.get("/upload").status_code == 200
    roles.users["frodo"]["groups"] = ["family"]       # demoted at the IdP, still admitted
    age_check(RECHECK + 1)
    assert browser.get("/upload").status_code == 403
    assert browser.get("/api/status").status_code == 200


def test_role_comes_from_the_server_row_not_a_replayed_cookie(roles, browser):
    sign_in(browser, roles, "frodo", "/")
    old = browser.cookies["session"]                  # a copy taken while still an editor
    roles.users["frodo"]["groups"] = ["family"]
    age_check(RECHECK + 1)
    assert browser.get("/upload").status_code == 403
    assert sql("SELECT groups FROM auth_sessions")[0]["groups"] == ["family"]
    browser.cookies.clear(); browser.cookies.set("session", old)
    assert browser.get("/upload").status_code == 403  # the old cookie's groups grant nothing
    roles.users["frodo"]["groups"] = ["admins"]       # promotion applies the same way
    age_check(RECHECK + 1)
    assert browser.get("/upload").status_code == 200


def test_a_row_without_groups_is_rechecked_at_once(roles, browser):
    sign_in(browser, roles, "frodo", "/")
    roles.users["frodo"]["groups"] = ["family"]
    sql("UPDATE auth_sessions SET groups = NULL")     # a row signed in before the groups column existed
    assert browser.get("/upload").status_code == 403
    assert sql("SELECT groups FROM auth_sessions")[0]["groups"] == ["family"]


def test_a_form_post_after_the_session_ended_lands_on_the_login_page(idp, browser):
    sign_in(browser, idp)
    assert browser.get("/reviews/prompt").status_code == 200                                 # the form (and its CSRF token) is rendered
    sql("UPDATE auth_sessions SET expires_at = now() - interval '1 second'")
    r = browser.post("/reviews/prompt", data={"instructions": "a long hand-written prompt"})
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/reviews/prompt"   # 307 would re-POST to the GET-only /login
    assert browser.get(r.headers["location"]).status_code == 200
    assert browser.get("/upload").status_code == 307                                         # safe methods keep 307


def test_unreachable_provider_at_sign_in_start_is_a_readable_error_not_a_500(client, monkeypatch):
    """The discovery document is fetched on the first sign-in: a provider the container cannot reach (split DNS, no hairpin NAT)
    must land on the login page with a message, and be logged, rather than a bare 500."""
    from app import auth as auth_mod

    async def boom(request, nxt):
        raise httpx2.ConnectError("name or service not known")
    monkeypatch.setattr(auth_mod, "start_login", boom)
    r = client.get("/auth/start?next=/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?error=")
    from urllib.parse import unquote
    assert "cannot reach the identity provider: ConnectError" in unquote(r.headers["location"])   # login.html shows `error`
