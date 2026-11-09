"""CSRF: one app-level check on every unsafe request, token from the session, Bearer exemption seam. Rejection tests use `raw_client`."""
from __future__ import annotations
import re

from conftest import csrf_token_of


def form_token(c) -> str:
    html = c.get("/settings").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_forms_render_a_per_session_token(raw_client):
    a, b = form_token(raw_client), form_token(raw_client)
    assert a == b and len(a) >= 32
    assert csrf_token_of(raw_client) == a


def test_post_without_token_is_403_with_a_flash(raw_client, reset_db):
    raw_client.get("/upload")
    r = raw_client.post("/rebuild")
    assert r.status_code == 403 and "could not be verified" in r.text
    assert client_untouched(raw_client)


def client_untouched(c):
    return c.get("/api/status").json()["months"] == 0


def test_wrong_token_rejected_right_token_accepted_as_field_or_header(raw_client, reset_db):
    tok = form_token(raw_client)
    assert raw_client.post("/rebuild", data={"csrf_token": "nope"}).status_code == 403
    assert raw_client.post("/rebuild", headers={"X-CSRF-Token": "nope"}).status_code == 403
    assert raw_client.post("/rebuild", data={"csrf_token": tok}).status_code != 403
    assert raw_client.post("/rebuild", headers={"X-CSRF-Token": tok}).status_code != 403


def test_other_session_token_rejected(raw_client, reset_db):
    from conftest import make_client
    other = make_client(csrf=False)
    stolen = form_token(other)
    form_token(raw_client)
    assert raw_client.post("/rebuild", data={"csrf_token": stolen}).status_code == 403


def test_a_new_route_is_covered_without_opting_in(raw_client, reset_db):
    from app.main import app
    n = len(app.router.routes)
    app.add_api_route("/_csrf_probe", lambda: {"ok": True}, methods=["POST", "PUT", "PATCH", "DELETE", "GET"])
    try:
        tok = form_token(raw_client)
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            assert raw_client.request(m, "/_csrf_probe").status_code == 403, m
            assert raw_client.request(m, "/_csrf_probe", headers={"X-CSRF-Token": tok}).status_code == 200, m
        assert raw_client.get("/_csrf_probe").status_code == 200   # safe methods never need a token
    finally:
        del app.router.routes[n:]


def test_multipart_upload_is_checked_and_still_parses(raw_client, reset_db):
    from conftest import GOLDEN
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted((GOLDEN / "exports").glob("*.csv"))]
    assert raw_client.post("/upload", files=files).status_code == 403
    tok = form_token(raw_client)
    r = raw_client.post("/upload", files=files, data={"csrf_token": tok})
    assert r.status_code == 303 and raw_client.get("/api/status").json()["months"] == 4


def test_json_clients_get_json_403(raw_client, reset_db):
    r = raw_client.post("/rebuild", headers={"Accept": "application/json"})
    assert r.status_code == 403 and "detail" in r.json()


def test_a_bearer_header_alone_does_not_skip_the_check(raw_client, reset_db):
    """No bearer authentication exists yet, so such a request is still authorised by the session cookie (#9)."""
    form_token(raw_client)
    for h in ("Bearer hl_example", "bearer junk", "Basic abc"):
        assert raw_client.post("/rebuild", headers={"Authorization": h}).status_code == 403, h
    assert client_untouched(raw_client)


def test_exemption_is_one_replaceable_seam(raw_client, reset_db, monkeypatch):
    from app import csrf
    monkeypatch.setattr(csrf, "csrf_exempt", lambda request: True)
    assert raw_client.post("/rebuild").status_code != 403


def test_shared_client_sends_a_valid_token_automatically(client, reset_db):
    assert client.post("/rebuild").status_code != 403
