"""Stale-data indicator: the date is injected, so nothing here depends on today."""
from __future__ import annotations
import datetime as dt

import pytest

from app import db, stale
from test_oidc import idp, browser, roles, sign_in  # noqa: F401  (fixtures)
from test_ops import parse

D = dt.date


@pytest.mark.parametrize("months,on,missing,message", [
    (["2026-09", "2026-10"], D(2026, 11, 9), [], None),                     # previous calendar month loaded
    (["2026-09", "2026-10", "2026-11"], D(2026, 11, 9), [], None),          # ahead of schedule
    (["2026-09"], D(2026, 11, 9), ["2026-10"], "October's exports are not loaded yet"),
    (["2026-09"], D(2026, 11, 1), ["2026-10"], "October's exports are not loaded yet"),
    (["2026-09"], D(2026, 10, 31), [], None),                               # October not due until November
    (["2026-09"], D(2027, 1, 5), ["2026-10", "2026-11", "2026-12"], "October, November and December: exports are not loaded yet"),
    (["2026-11"], D(2027, 1, 5), ["2026-12"], "December's exports are not loaded yet"),   # year rollover
    (["2024-03", "2025-12"], D(2026, 2, 2), ["2026-01"], "January's exports are not loaded yet"),   # unordered input, latest wins
    ([], D(2026, 11, 9), [], None),                                         # nothing loaded: nothing is late
])
def test_stale_status(months, on, missing, message):
    r = stale.stale_status(months, on)
    assert r["months_missing"] == stale.missing_months(months, on) == missing and r["stale"] is bool(missing) and r["message"] == message


def test_expected_month_rolls_over_the_year():
    assert stale.expected_month(D(2027, 1, 1)) == "2026-12" and stale.expected_month(D(2026, 3, 31)) == "2026-02"


@pytest.fixture
def loaded(client, reset_db, monkeypatch):
    def go(months, on):
        reset_db()
        with db.pool.connection() as conn:
            for m in months:
                conn.execute("INSERT INTO source_files (kind, budget_month, filename, sha256, row_count) VALUES ('transactions', %s, %s, %s, 0)", (m, f"x-{m}.csv", m))
        monkeypatch.setattr(stale, "today", lambda: on)
        return client
    return go


def test_status_banner_and_metric_when_a_month_is_missing(loaded):
    c = loaded(["2026-08", "2026-09"], D(2026, 11, 9))
    s = c.get("/api/status").json()
    assert s["stale"] is True and s["months_missing"] == ["2026-10"] and s["expected_month"] == "2026-10" and s["latest_month"] == "2026-09" and s["last"] == "2026-09"
    page = c.get("/").text
    assert 'id="stale-banner"' in page and "October&#39;s exports are not loaded yet" in page and 'href="/upload">Close a month' in page
    assert parse(c.get("/metrics").text)["ledger_months_missing"] == 1


def test_no_banner_when_current_or_empty(loaded):
    for months in (["2026-09", "2026-10"], []):
        c = loaded(months, D(2026, 11, 9))
        s = c.get("/api/status").json()
        assert s["stale"] is False and s["months_missing"] == [] and s["message"] is None and "stale-banner" not in c.get("/").text
        assert parse(c.get("/metrics").text)["ledger_months_missing"] == 0


def test_viewer_sees_the_banner_without_the_link(loaded, roles, browser):
    loaded(["2026-09"], D(2026, 11, 9))
    sign_in(browser, roles, "rosie", "/")
    page = browser.get("/").text
    assert 'id="stale-banner"' in page and 'href="/upload"' not in page
