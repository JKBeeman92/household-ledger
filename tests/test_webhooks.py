"""Outbound hooks (#35). Delivery goes through a stub httpx transport; nothing here touches the network."""
from __future__ import annotations
import hashlib, hmac, json, time

import httpx
import pytest

from conftest import GOLDEN, upload_files
from test_oidc import sql  # noqa: F401
from app import hooks, review

URL = "https://ntfy.bagend.lan/ledger?topic=second-breakfast"
SECRET = "elevenses-0123456789"


class Stub:
    """Records every request the hooks send; `reply` is the status (or an exception) to answer with."""
    def __init__(self):
        self.reqs, self.reply = [], 200

    def __call__(self, request: httpx.Request):
        self.reqs.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return httpx.Response(self.reply, headers={"Location": "http://169.254.169.254/"} if 300 <= self.reply < 400 else {})


@pytest.fixture
def stub(monkeypatch):
    s = Stub()
    monkeypatch.setattr(hooks, "TRANSPORT", httpx.MockTransport(s))
    return s


def configure(client, url=URL, secret=SECRET, enabled="1"):
    return client.post("/webhooks", data={"url": url, "secret": secret, "enabled": enabled})


def deliveries():
    return sql("SELECT * FROM webhook_deliveries ORDER BY id")


def body(req) -> dict:
    return json.loads(req.content)


def test_page_is_for_editors_and_never_shows_the_secret(client, reset_db, raw_client):
    r = client.get("/webhooks")
    assert r.status_code == 200 and 'name="csrf_token"' in r.text and 'action="/webhooks/test"' in r.text
    assert 'href="/webhooks"' in client.get("/settings").text
    assert configure(client).status_code == 303
    page = client.get("/webhooks").text
    assert SECRET not in page and URL in page.replace("&amp;", "&") and "A secret is stored" in page and 'checked' in page
    assert raw_client.post("/webhooks", data={"url": URL, "secret": SECRET}).status_code == 403   # CSRF applies


def test_save_validates_and_blank_secret_keeps_the_stored_one(client, reset_db):
    for bad in ("ftp://x.lan/hook", "file:///etc/passwd", "https://frodo:ring@x.lan/h", "http://169.254.169.254/latest", "not a url"):
        configure(client, url=bad)
        assert "Not saved" in client.get("/webhooks").text, bad
        assert not sql("SELECT 1 FROM settings WHERE key='webhook'")
    configure(client, secret="")   # enabling needs a secret
    assert "needs a URL and a shared secret" in client.get("/webhooks").text and not sql("SELECT 1 FROM settings WHERE key='webhook'")
    configure(client)
    configure(client, url="http://homeassistant.lan:8123/api/webhook/x", secret="", enabled="")
    cfg = sql("SELECT value FROM settings WHERE key='webhook'")[0]["value"]
    assert cfg == {"url": "http://homeassistant.lan:8123/api/webhook/x", "secret": SECRET, "enabled": False}
    configure(client, url="", secret="", enabled="")   # clearing the URL removes the hook and the secret
    assert sql("SELECT value FROM settings WHERE key='webhook'")[0]["value"] == {"url": "", "secret": "", "enabled": False}


def test_test_event_is_signed_with_a_timestamp_and_logged(client, reset_db, stub):
    assert "Save a URL" in (client.post("/webhooks/test"), client.get("/webhooks").text)[1]
    configure(client, enabled="")   # a test goes out even while events are switched off
    assert client.post("/webhooks/test").status_code == 303
    (req,) = stub.reqs
    ts = req.headers["x-ledger-timestamp"]
    assert abs(time.time() - int(ts)) < 30 and req.headers["x-ledger-event"] == "test" and req.headers["content-type"] == "application/json"
    want = "sha256=" + hmac.new(SECRET.encode(), ts.encode() + b"." + req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-ledger-signature"] == want and hooks.sign(SECRET, ts, req.content) == want
    assert hooks.sign(SECRET, str(int(ts) + 1), req.content) != want   # the timestamp is covered by the signature
    p = body(req)
    assert p["event"] == "test" and p["dashboard_url"] == "http://testserver/" and p["id"] == req.headers["x-ledger-delivery"]
    (row,) = deliveries()
    assert row["ok"] and row["http_status"] == 200 and row["event"] == "test" and row["target"] == "https://ntfy.bagend.lan" and "second-breakfast" not in json.dumps(row, default=str)
    page = client.get("/webhooks").text
    assert "Delivered" in page and "ntfy.bagend.lan" in page


def test_build_completed_after_a_successful_rebuild(client, reset_db, stub):
    configure(client)
    upload_files(client, GOLDEN / "exports")
    evs = [body(r) for r in stub.reqs]
    assert [e["event"] for e in evs] == ["build.completed"]
    e = evs[0]
    assert e["month"] and set(e["figures"]) == {"income", "spend", "debt", "net"} and e["reconciliation"] == {"status": "clean", "problems": 0}
    assert e["dashboard_url"] == "http://testserver/" and e["currency"] == "USD"
    assert "transactions" not in e and "payee" not in json.dumps(e).lower()   # derived figures only
    assert stub.reqs[0].url == httpx.URL(URL) and sql("SELECT ok, event, month FROM webhook_deliveries")[0]["event"] == "build.completed"


def test_nothing_is_sent_when_disabled_or_unconfigured(client, reset_db, stub):
    upload_files(client, GOLDEN / "exports")
    configure(client, enabled="")
    client.post("/rebuild")
    assert stub.reqs == [] and deliveries() == []


@pytest.mark.parametrize("reply", [500, 302, httpx.ConnectTimeout("slow"), httpx.ConnectError("refused")])
def test_a_failing_receiver_is_logged_and_never_breaks_the_build(client, reset_db, stub, reply):
    configure(client)
    stub.reply = reply
    r = upload_files(client, GOLDEN / "exports")
    assert r.status_code in (200, 303)
    (row,) = deliveries()
    assert not row["ok"] and row["error"]
    assert sql("SELECT ok FROM build_log ORDER BY id DESC LIMIT 1")[0]["ok"] is True   # the build itself stayed green
    assert len(stub.reqs) == 1   # no retry, and a redirect is not followed
    assert "Failed" in client.get("/webhooks").text


def test_short_timeout_and_no_redirects_are_configured(client, reset_db, stub):
    configure(client)
    seen = {}
    orig = stub.__call__
    stub_reqs = stub.reqs

    def spy(request):
        seen["timeout"] = request.extensions["timeout"]
        return orig(request)
    hooks.TRANSPORT = httpx.MockTransport(spy)
    client.post("/webhooks/test")
    assert 0 < seen["timeout"]["read"] <= 10 and 0 < seen["timeout"]["connect"] <= 10 and hooks.TIMEOUT <= 10
    stub.reply = 307
    client.post("/webhooks/test")
    assert len(stub_reqs) == 2 and not deliveries()[-1]["ok"] and deliveries()[-1]["error"] == "redirect not followed"


def test_review_stored_event(client, reset_db, stub):
    upload_files(client, GOLDEN / "exports")
    configure(client)
    month = sql("SELECT max(budget_month) m FROM source_files")[0]["m"]
    from app import db
    with db.pool.connection() as conn:
        review.save_agent(conn, month, [{"order": 1, "heading": "Second breakfast", "text": "Fine."}], "agent via MCP (test)")
        for th in __import__("threading").enumerate():
            if th.name == "webhook":
                th.join(10)
    (req,) = stub.reqs
    e = body(req)
    assert e["event"] == "review.stored" and e["month"] == month and e["sections"] == 1 and e["reconciliation"]["status"] == "clean"
    assert "Second breakfast" not in req.content.decode()   # no review prose, only that one was stored


def test_months_missing_fires_once_per_missing_month(client, reset_db, stub):
    configure(client)
    assert hooks.notify_missing_months([]) is None
    assert hooks.notify_missing_months(["2026-09"])["ok"]
    assert hooks.notify_missing_months(["2026-09"]) is None                      # same month: not again
    assert hooks.notify_missing_months(["2026-09", "2026-10"])["ok"]             # only the new one is announced
    assert [body(r)["months"] for r in stub.reqs] == [["2026-09"], ["2026-10"]]
    assert body(stub.reqs[0])["event"] == "months.missing"
    assert hooks.notify_missing_months([]) is None                               # loaded since: forgotten ...
    assert hooks.notify_missing_months(["2026-10"])["ok"] and len(stub.reqs) == 3   # ... so losing it again is news again


def test_months_missing_waits_for_a_configured_hook(client, reset_db, stub):
    assert hooks.notify_missing_months(["2026-09"]) is None and stub.reqs == []
    configure(client)
    assert hooks.notify_missing_months(["2026-09"])["ok"]   # not marked as announced while nobody was listening


def test_stale_check_announces_missing_months_from_the_loaded_files(client, reset_db, stub):
    import datetime
    from app import stale
    upload_files(client, GOLDEN / "exports")
    latest = sql("SELECT max(budget_month) m FROM source_files")[0]["m"]
    y, mo = int(latest[:4]), int(latest[5:7])
    two_later = datetime.date(y + (mo + 3 > 12), (mo + 2) % 12 + 1, 15)   # the 15th, three calendar months after the latest loaded one
    configure(client)
    stub.reqs.clear()
    assert stale.announce(two_later)["ok"]
    e = body(stub.reqs[-1])
    assert e["event"] == "months.missing" and len(e["months"]) == 2 and min(e["months"]) > latest
    assert stale.announce(two_later) is None   # each month is announced once
