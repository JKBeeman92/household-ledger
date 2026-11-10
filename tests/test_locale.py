"""Currency and locale come from the household setting, reach the page once, and no formatter hard-codes USD / en."""
from __future__ import annotations
import re
from pathlib import Path

import pytest

from app import db
from app.web_helpers import _format_settings

WEB = Path(__file__).resolve().parent.parent / "app" / "web"


def _household(**kw):
    with db.pool.connection() as conn:
        db.put_setting(conn, "household", {"name": "Bag End", "title": "Bag End Ledger", **kw})


def test_page_carries_the_household_currency_and_locale(client, reset_db):
    _household(currency="eur", locale="de-DE")
    html = client.get("/").text
    assert '<meta name="ledger-currency" content="EUR">' in html and '<meta name="ledger-locale" content="de-DE">' in html
    s = client.get("/api/status").json()
    assert s["currency"] == "EUR" and s["locale"] == "de-DE"


def test_defaults_when_unset_and_bad_values_fall_back(client, reset_db):
    _household()
    assert '<meta name="ledger-currency" content="USD">' in client.get("/").text
    assert _format_settings({"currency": "<x>", "locale": '"><script>'}) == {"currency": "USD", "locale": "en-US"}
    assert _format_settings({"currency": "GBP", "locale": "en-GB"}) == {"currency": "GBP", "locale": "en-GB"}


def test_no_formatter_hard_codes_usd_or_english():
    page = (WEB / "templates" / "dashboard.html").read_text()
    for pat in (r"currency:\s*'USD'", r"Intl\.NumberFormat\('en'", r"d3\.format\(", r"d3\.utcFormat\('%b"):
        assert not re.search(pat, page), pat
    assert "dash.fmt.money0" in page and "dash.fmt.compact" in page


def test_page_text_says_price_index(client, reset_db):
    html = client.get("/").text
    assert "Price index used" in html and "CPI-adjusted" not in html and "CPI-U used" not in html
    assert "CPI-U" in html  # kept as the documented US source in the Method text
