"""Web routes against a real Postgres (DATABASE_URL), AUTH_DISABLED=true except where a test turns auth on."""
from __future__ import annotations
import dataclasses, json

import pytest

from conftest import GOLDEN, RECON_FAIL, upload_files

EXPECTED = GOLDEN / "expected" / "dash_data"


def flashes_after(client, resp) -> str:
    assert resp.status_code == 303 and resp.headers["location"] in ("/upload", "/settings")
    return client.get(resp.headers["location"]).text


@pytest.fixture
def auth_on(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_dashboard_page(client, reset_db):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "dash-shim.js" in r.text


def test_datasets_empty_before_any_upload(client, reset_db):
    r = client.get("/api/datasets")
    assert r.status_code == 200 and r.json() == {}


def test_upload_builds_the_golden_datasets(client, reset_db):
    r = upload_files(client, GOLDEN / "exports")
    assert r.status_code == 303 and r.headers["location"] == "/upload"
    page = client.get("/upload").text
    assert "Reconciliation:" not in page and "Stored" in page
    assert client.get("/api/status").json()["months"] == 4
    data = client.get("/api/datasets").json()
    want = {p.stem: json.loads(p.read_text()) for p in EXPECTED.glob("*.json")}
    assert set(want) <= set(data)
    assert all(isinstance(rows, list) for rows in data.values())
    for name, rows in want.items():
        assert data[name] == rows, name
    assert client.get("/api/dataset/monthly").json() == want["monthly"]
    assert client.get("/api/dataset/nope").status_code == 404


def test_upload_with_reconciliation_failure_is_reported(client, reset_db):
    r = upload_files(client, RECON_FAIL / "exports")
    page = flashes_after(client, r)
    assert "Reconciliation:" in page and "MISMATCH" in page and "ITEM_NOT_IN_SUMMARY" in page


def test_upload_rejects_a_file_that_is_not_an_export(client, reset_db):
    r = client.post("/upload", files=[("files", ("notes.csv", b"a,b\n1,2\n", "text/csv"))])
    assert "not an EveryDollar export filename" in flashes_after(client, r)
    assert client.get("/api/status").json()["months"] == 0


GOOD_MAPPING = "Item,CanonicalItem,Category,Flow,Note\nRent,Rent,Housing,expense,\nGroceries,Groceries,Food,expense,\n"


def test_mapping_rejects_a_bad_flow(client, reset_db):
    from app import db
    with db.pool.connection() as conn:
        before = conn.execute("SELECT item, flow FROM item_mapping ORDER BY item").fetchall()
    r = client.post("/settings/mapping", data={"body": GOOD_MAPPING.replace("Groceries,Food,expense", "Groceries,Food,bogus")})
    page = flashes_after(client, r)
    assert "Not saved" in page and "bad Flow on: Groceries" in page
    with db.pool.connection() as conn:
        assert conn.execute("SELECT item, flow FROM item_mapping ORDER BY item").fetchall() == before


def test_mapping_accepts_a_good_body(client, reset_db):
    from app import db
    r = client.post("/settings/mapping", data={"body": GOOD_MAPPING})
    assert "Saved 2 mapping rows" in flashes_after(client, r)
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM item_mapping").fetchone()["n"] == 2


def test_unknown_settings_editor_is_404(client, reset_db):
    assert client.post("/settings/nope", data={"body": "x"}).status_code == 404


def test_api_requires_sign_in_when_auth_is_on(client, reset_db, auth_on):
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/status").status_code == 401
    r = client.get("/")
    assert r.status_code == 307 and r.headers["location"] == "/login?next=/"
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("path", ["/login?next=//evil.example", "/login?next=//evil.example/path", "/auth/start?next=//evil.example"])
def test_no_redirect_off_site_when_signed_in(client, reset_db, path):
    r = client.get(path)
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/"


def test_login_page_does_not_redirect_when_signed_out(client, reset_db, auth_on):
    r = client.get("/login?next=//evil.example")
    assert r.status_code == 200 and "location" not in r.headers


def test_datasets_are_gzipped_and_revalidated_with_etag(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    r = client.get("/api/datasets", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert r.headers["cache-control"] == "private, no-cache" and r.headers["etag"]
    etag = r.headers["etag"]
    r2 = client.get("/api/datasets", headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == etag
    assert client.get("/api/datasets", headers={"If-None-Match": 'W/"nope", ' + etag}).status_code == 304
    assert client.get("/api/datasets", headers={"If-None-Match": '"stale"'}).status_code == 200
    upload_files(client, GOLDEN / "exports")  # rebuild: datasets are rewritten, so the tag changes
    r3 = client.get("/api/datasets", headers={"If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["etag"] != etag


def test_datasets_304_still_requires_sign_in(client, reset_db, auth_on):
    assert client.get("/api/datasets", headers={"If-None-Match": "*"}).status_code == 401
