"""/metrics, /healthz build status and structured logging."""
from __future__ import annotations
import io, json, logging

import pytest

from conftest import GOLDEN, RECON_FAIL, upload_files
from test_routes import auth_on  # noqa: F401  (fixture)


def parse(text: str) -> dict[str, float]:
    return {l.split()[0]: float(l.split()[1]) for l in text.splitlines() if l and not l.startswith("#")}


def test_metrics_empty_database(client, reset_db):
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    m = parse(r.text)
    assert m["ledger_months_loaded"] == 0 and m["ledger_reconciliation_problems"] == 0
    assert "ledger_last_build_ok" not in m and "ledger_last_review_timestamp_seconds" not in m
    assert "# TYPE ledger_months_loaded gauge" in r.text


def test_metrics_after_build(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    m = parse(client.get("/metrics").text)
    assert m["ledger_months_loaded"] >= 1 and m["ledger_last_build_ok"] == 1
    assert m["ledger_last_build_duration_seconds"] >= 0 and m["ledger_last_build_timestamp_seconds"] > 1e9
    assert m["ledger_reconciliation_problems"] == 0


def test_metrics_counts_reconciliation_problems_and_review(client, reset_db):
    upload_files(client, RECON_FAIL / "exports")
    from app import db
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO reviews (month, model, sections, prompt) VALUES ('2026-01', 'm', '[]', 'p')")
    m = parse(client.get("/metrics").text)
    assert m["ledger_reconciliation_problems"] > 0 and m["ledger_last_review_timestamp_seconds"] > 1e9


def test_metrics_requires_sign_in(client, reset_db, auth_on):
    assert client.get("/metrics").status_code == 401


def test_healthz_reports_build_without_failing(client, reset_db, auth_on):
    r = client.get("/healthz")  # unauthenticated
    assert r.status_code == 200 and r.json() == {"ok": True, "last_build": "none"}
    from app import db
    def log_build(ok):
        with db.pool.connection() as conn:
            conn.execute("INSERT INTO build_log (finished_at, ok, summary) VALUES (now(), %s, 'x')", (ok,))
    log_build(True)
    assert client.get("/healthz").json()["last_build"] == "ok"
    log_build(False)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True, "last_build": "failed"}


def test_request_id_header(client):
    r = client.get("/healthz")
    assert len(r.headers["x-request-id"]) == 32
    assert client.get("/healthz", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    assert client.get("/healthz", headers={"X-Request-ID": "bad id\x01"}).headers["x-request-id"] != "bad id\x01"


def test_json_formatter_and_access_line(client):
    from app import logging_setup as ls
    buf = io.StringIO()
    h = logging.StreamHandler(buf); h.setFormatter(ls.JsonFormatter())
    ls.access_log.addHandler(h); ls.access_log.setLevel(logging.INFO)
    try:
        client.get("/healthz")
        client.get("/api/status", headers={"X-Request-ID": "req-42"})
    finally:
        ls.access_log.removeHandler(h)
    lines = [json.loads(l) for l in buf.getvalue().splitlines()]
    assert len(lines) == 1  # /healthz is not logged
    e = lines[0]
    assert e["request_id"] == "req-42" and e["path"] == "/api/status" and e["status"] == 200 and e["level"] == "INFO" and e["duration_ms"] >= 0


def test_configure_is_opt_in(monkeypatch):
    from app import logging_setup as ls
    root = logging.getLogger()
    before = list(root.handlers)
    ls.configure("")
    assert root.handlers == before
    monkeypatch.setattr(root, "handlers", list(before))
    ls.configure("json")
    assert isinstance(root.handlers[0].formatter, ls.JsonFormatter)
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "disabled", False)
