"""DS-WORD-6 on the server (#190): money, dates, months and shares in the household's currency and locale on every server-rendered
page, the same CLDR data the dashboard's Intl uses; a missing figure is an em dash, a field's value stays a plain number."""
from __future__ import annotations
import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import GOLDEN, upload_files
from app import db, formatting as f

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"


# ---- the formatters ----

@pytest.mark.parametrize("cur,loc,want", [
    ("USD", "en-US", "$1,234,568"), ("EUR", "de-DE", "1.234.568\xa0€"), ("INR", "en-IN", "₹12,34,568"),
    ("CHF", "fr-CH", "1 234 568\xa0CHF"), ("GBP", "en-GB", "£1,234,568"),
])
def test_money_follows_currency_and_locale(cur, loc, want):
    assert f.money(1234567.891, cur, loc) == want


def test_money_cents_signs_and_missing():
    assert f.money(-12.5, "USD", "en-US", 2) == "−$12.50"
    assert f.money(Decimal("2756"), signed=True) == "+$2,756" and f.money(-2557, signed=True) == "−$2,557" and f.money(0, signed=True) == "$0"
    assert f.money(None) == f.money("") == f.money("n/a") == "—"   # never a zero
    assert f.money(5, "ZZZ") != "—"                                   # an unknown code still shows the amount


def test_dates_months_and_shares():
    assert f.date(dt.date(2026, 11, 1)) == "1 Nov 2026" and f.date(dt.datetime(2026, 11, 1, 14, 5)) == "1 Nov 2026"
    assert f.datetime(dt.datetime(2026, 11, 1, 14, 5, 9)) == "1 Nov 2026, 14:05"
    assert f.month("2026-11") == "November 2026" and f.month_short("2026-11") == "Nov 2026"
    assert f.month("2026-11", "de-DE") == "November 2026" and f.month_short("2026-11", "fr-FR") == "nov. 2026"
    assert f.date(None) == f.month(None) == "—" and f.month("not a month") == "not a month"
    assert f.share(0.1) == "10%" and f.share(0.0655, places=1) == "6.6%" and f.share(0.065, places=3, trim=True) == "6.5%"
    assert f.share(0.1, "de-DE", 1) == "10,0\xa0%" and f.share(-0.02, signed=True) == "−2%"
    assert f.number(12345) == "12,345" and f.number(12345, "de-DE") == "12.345" and f.number(3.14159, places=1) == "3.1"
    assert f.plain(Decimal("0.1000")) == "0.1" and f.plain(Decimal("1200.00")) == "1200" and f.plain(None) == ""


# ---- no template prints an ISO date or an unformatted amount to a person ----

def test_no_template_formats_by_hand():
    bad = re.compile(r"strftime\(|'\{:[+,.0-9]*f\}'\.format|\"\{:,\}\"\.format|'\{:,\}'\.format|'%\.[0-9]f'\|format")
    field = re.compile(r'value="\{\{.*?\}\}"')   # a field's value= is what a person types back: it stays a plain number (DS-WORD-6)
    hits = [f"{p.relative_to(TEMPLATES)}:{i}" for p in TEMPLATES.rglob("*.html") for i, line in enumerate(p.read_text().splitlines(), 1)
            if bad.search(field.sub("", line))]
    assert not hits, hits


# ---- the pages, for a household that is neither USD nor en-US ----

@pytest.fixture
def euro_household(client, reset_db):
    upload_files(client, GOLDEN / "exports")   # 2025-01 … 2025-04, built
    with db.pool.connection() as conn:
        hh = db.get_setting(conn, "household", {})
        db.put_setting(conn, "household", {**hh, "currency": "EUR", "locale": "de-DE"})
        conn.commit()


ISO_MONTH_TEXT = re.compile(r">[^<>]*\b20\d\d-(0[1-9]|1[0-2])\b(?!-\d)[^<>]*<")   # "2025-04" as text between tags, not in an attribute


def _text_months(html: str) -> list[str]:
    body = re.sub(r"<(script|style|textarea)\b.*?</\1>", "", html, flags=re.S)
    return [m.group(0) for m in ISO_MONTH_TEXT.finditer(body)]


@pytest.mark.parametrize("path", ["/upload", "/checkin/2025-04", "/reviews", "/reviews/history", "/settings", "/settings/tokens", "/webhooks", "/settings/sources"])
def test_pages_show_months_as_words(client, euro_household, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert not _text_months(r.text), (path, _text_months(r.text)[:5])


def test_close_a_month_and_check_in_speak_euros_and_german_months(client, euro_household):
    up = client.get("/upload").text
    assert "Apr. 2025" in up and "2025-04</td>" not in up
    ci = client.get("/checkin/2025-04").text
    assert "April 2025" in ci
    standing = ci[ci.find('id="step-standing"'):]
    assert "€" in standing and "$" not in standing[:4000]


@pytest.mark.parametrize("call,want", [
    (lambda: f.money(1234.5), "$1,235"), (lambda: f.money(2.5), "$3"), (lambda: f.money(0.5), "$1"),
    (lambda: f.money(-0.4), "$0"), (lambda: f.money(-0.4, signed=True), "$0"), (lambda: f.number(-0.4), "0"),
    (lambda: f.share(0.125), "13%"), (lambda: f.share(-0.004), "0%"), (lambda: f.money(-2.5), "−$3"),
])
def test_half_way_rounds_away_from_zero_like_intl_and_zero_has_no_sign(call, want):
    """DS-WORD-6: the server reads a figure as the dashboard's Intl does (half away from zero), and a figure shown as zero has no sign."""
    assert call() == want
