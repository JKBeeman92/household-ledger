"""The access line (#111): the client without uvicorn's ':0', the peer it came through, in text and JSON alike."""
from __future__ import annotations
import asyncio, io, json, logging

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from conftest import ROOT
from app import logging_setup as ls


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def request(trusted: str, peer: str, xff: str | None, path: str = "/settle") -> logging.LogRecord:
    app = ls.RequestIdMiddleware(ProxyHeadersMiddleware(_ok, trusted_hosts=trusted))   # the order app/main.py builds
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    scope = {"type": "http", "method": "GET", "path": path, "headers": headers, "client": (peer, 51234), "scheme": "http"}
    records = []
    h = logging.Handler(); h.emit = records.append
    ls.access_log.addHandler(h); old = ls.access_log.level; ls.access_log.setLevel(logging.INFO)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(_):
        pass
    try:
        asyncio.run(app(scope, receive, send))
    finally:
        ls.access_log.removeHandler(h); ls.access_log.setLevel(old)
    return records[0] if records else None


def test_proxied_request_names_client_and_peer_without_a_port():
    r = request("172.18.0.1", "172.18.0.1", "203.0.113.7")
    assert r.getMessage() == '203.0.113.7 via 172.18.0.1 "GET /settle" 204 ' + f"{r.fields['duration_ms']}ms"
    assert ":0" not in r.getMessage()
    assert {k: r.fields[k] for k in ("client", "peer", "method", "path", "status")} == \
        {"client": "203.0.113.7", "peer": "172.18.0.1", "method": "GET", "path": "/settle", "status": 204}


def test_untrusted_peer_is_its_own_client_so_the_log_shows_the_value_for_forwarded_allow_ips():
    r = request("192.0.2.1", "172.18.0.1", "203.0.113.7")   # the README's "trust nobody" address
    assert r.fields["client"] == r.fields["peer"] == "172.18.0.1" and " via " not in r.getMessage()


def test_star_trusts_everyone_and_still_shows_the_proxy():
    r = request("*", "172.18.0.1", "203.0.113.7, 10.0.0.9")
    assert r.fields["peer"] == "172.18.0.1" and r.fields["client"] in ("203.0.113.7", "10.0.0.9")


def test_healthz_stays_quiet():
    assert request("*", "127.0.0.1", None, path="/healthz") is None


def test_json_line_keeps_its_field_names_and_adds_client_and_peer():
    r = request("172.18.0.1", "172.18.0.1", "203.0.113.7")
    e = json.loads(ls.JsonFormatter().format(r))
    assert {"ts", "level", "logger", "msg", "request_id", "method", "path", "status", "duration_ms", "client", "peer"} <= set(e)


def test_text_mode_configures_an_unconfigured_root_once_so_later_basicconfig_is_a_no_op(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", []); monkeypatch.setattr(root, "level", logging.WARNING)
    ls.configure(""); ls.configure("")
    assert len(root.handlers) == 1 and root.handlers[0].formatter._fmt == ls.TEXT_FORMAT and root.level == logging.INFO
    logging.basicConfig(format="%(message)s")   # what the mcp SDK does at start-up: must not add a bare second line
    assert len(root.handlers) == 1
    buf = io.StringIO(); root.handlers[0].setStream(buf)
    logging.getLogger("ledger.access").info('203.0.113.7 via 172.18.0.1 "GET /" 200 1.0ms')
    assert buf.getvalue() == 'INFO     ledger.access: 203.0.113.7 via 172.18.0.1 "GET /" 200 1.0ms\n'


def test_app_applies_proxy_headers_inside_the_access_logger():
    from app.main import app
    order = [m.cls for m in app.user_middleware]   # outermost first
    assert order[0] is ls.RequestIdMiddleware and order[1] is ProxyHeadersMiddleware


def test_image_turns_off_uvicorns_own_proxy_headers_and_access_log():
    cmd = next(l for l in (ROOT / "Dockerfile").read_text().splitlines() if l.startswith("CMD"))
    assert "--no-proxy-headers" in cmd and "--no-access-log" in cmd and "--forwarded-allow-ips" not in cmd
