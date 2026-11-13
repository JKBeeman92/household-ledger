"""Money, dates, months and shares as a person reads them (docs/DESIGN.md, DS-WORD-6), for every server-rendered page.

The dashboard formats in script (`dash-shim.js`, Intl); these do the same on the server from the same CLDR data (Babel), so a
figure reads alike on both. Each takes the household's currency (ISO 4217) and locale (BCP 47), already cleaned by
`web_helpers._format_settings`. A missing figure is an em dash, never a zero. What a person types into a field is not formatted:
a field's `value=` stays a plain number.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from babel import Locale, UnknownLocaleError
from babel.dates import format_date, format_skeleton
from babel.numbers import format_currency, format_decimal, format_percent

DASH = "—"
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})")


def _locale(loc: str | None) -> Locale:
    try:
        return Locale.parse((loc or "en-US").replace("-", "_"))
    except (ValueError, UnknownLocaleError):
        return Locale.parse("en_US")


def _number(v) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _round(d: Decimal, places: int) -> Decimal:
    """Half away from zero, as Intl does on the dashboard (DS-WORD-6: a figure reads alike on either side); Babel alone rounds half
    to even ($1,234.50 would be $1,234 here and $1,235 there). A result of zero carries no sign."""
    r = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return r.copy_abs() if r == 0 else r


def money(v, currency: str = "USD", locale: str = "en-US", places: int = 0, signed: bool = False) -> str:
    """1234.5 → "$1,235" (en-US, USD) or "1.235 €" (de-DE, EUR); places=2 keeps cents; signed=True writes +/− on a change."""
    d = _number(v)
    if d is None:
        return DASH
    d = _round(d, places)
    loc = _locale(locale)
    frac = "." + "0" * places if places else ""
    # the locale's own pattern (symbol placement, spacing, grouping such as #,##,##0), with our number of decimals
    pat = re.sub(r"0(\.0+)?(?![0#])", "0" + frac, loc.currency_formats["standard"].pattern.split(";")[0], count=1)
    try:
        s = format_currency(abs(d) if signed else d, currency, format=pat, locale=loc, currency_digits=False)
    except Exception:   # an unknown currency code: the number with the code beside it
        s = f"{format_decimal(abs(d) if signed else d, format='#,##0' + frac, locale=loc)} {currency}"
    if signed:
        s = ("+" if d > 0 else "−" if d < 0 else "") + s
    return s.replace("-", "−")


def number(v, locale: str = "en-US", places: int = 0) -> str:
    """A count or a plain figure, grouped for the locale: 12345 → "12,345"."""
    d = _number(v)
    if d is None:
        return DASH
    d = _round(d, places)
    return format_decimal(d, format="#,##0" + ("." + "0" * places if places else ""), locale=_locale(locale)).replace("-", "−")


def plain(v) -> str:
    """A stored number as a person would type it back into a field: Decimal('0.1000') → "0.1", 1200.00 → "1200". Not grouped."""
    d = _number(v)
    if d is None:
        return ""
    return f"{d.normalize():f}"


def share(v, locale: str = "en-US", places: int = 0, signed: bool = False, trim: bool = False) -> str:
    """A fraction as a percentage: 0.1 → "10%"; places=1 → "10.0%"; trim=True drops decimals that are zero (0.065 → "6.5%", 0.07 → "7%")."""
    d = _number(v)
    if d is None:
        return DASH
    d = _round(d * 100, places) / 100   # rounded as the percentage it is shown as, half away from zero
    loc = _locale(locale)
    frac = "." + ("#" if trim else "0") * places if places else ""
    pat = re.sub(r"0(\.[0#]+)?(?![0#])", "0" + frac, loc.percent_formats[None].pattern.split(";")[0], count=1)   # the locale's spacing ("10 %")
    s = format_percent(abs(d) if signed else d, format=pat, locale=loc)
    if signed:
        s = ("+" if d > 0 else "−" if d < 0 else "") + s
    return s.replace("-", "−")


def _as_date(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.date.fromisoformat(v[:10])
        except ValueError:
            m = _MONTH_RE.match(v)
            return dt.date(int(m[1]), int(m[2]), 1) if m and 1 <= int(m[2]) <= 12 else None
    return None


def date(v, locale: str = "en-US") -> str:
    """A day: 2026-11-01 → "1 Nov 2026"."""
    d, loc = _as_date(v), _locale(locale)
    if not d:
        return DASH
    # DS-WORD-6 writes a day as "1 Nov 2026"; a language other than English gets its own order ("2026年11月1日")
    return format_date(d, "d MMM y", locale=loc) if loc.language == "en" else format_skeleton("yMMMd", d, locale=loc)


def datetime(v, locale: str = "en-US") -> str:
    """A moment: "1 Nov 2026, 14:05". Times are shown as stored (the server's clock), to the minute."""
    if not isinstance(v, dt.datetime):
        return date(v, locale)
    return f"{date(v, locale)}, {v.strftime('%H:%M')}"


def time(v, locale: str = "en-US") -> str:
    """The time of day alone, for a moment earlier today: "14:05:09"."""
    return v.strftime("%H:%M:%S") if isinstance(v, (dt.datetime, dt.time)) else DASH


def month(v, locale: str = "en-US") -> str:
    """A budget month in a heading or a sentence: "2026-11" → "November 2026"."""
    d = _as_date(v)
    return format_skeleton("yMMMM", d, locale=_locale(locale)) if d else (str(v) if v else DASH)


def month_name(n, locale: str = "en-US") -> str:
    """A calendar month by its number, in the household's language: 2 → "February" (a yearly bill's month, #151)."""
    import datetime as _dt
    try:
        return format_skeleton("MMMM", _dt.date(2000, int(n), 1), locale=_locale(locale))
    except (TypeError, ValueError):
        return DASH


def month_short(v, locale: str = "en-US") -> str:
    """A budget month in a table or on a chart: "2026-11" → "Nov 2026"."""
    d = _as_date(v)
    return format_skeleton("yMMM", d, locale=_locale(locale)) if d else (str(v) if v else DASH)
