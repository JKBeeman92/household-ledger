"""Stale-data check: which budget months should be loaded by now but are not. Pure functions; `on` (today) is injectable."""
from __future__ import annotations
import datetime as _dt

_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def today() -> _dt.date:
    """Wrapped so tests can replace it; the functions below take an explicit date and default to this."""
    return _dt.date.today()


def expected_month(on: _dt.date) -> str:
    """The newest budget month that should be loaded on `on`: the previous calendar month."""
    return f"{on.year - (on.month == 1)}-{(on.month - 2) % 12 + 1:02d}"


def missing_months(months: list[str], on: _dt.date | None = None) -> list[str]:
    """Every month after the latest loaded one, through the previous calendar month ('YYYY-MM', oldest first).
    Nothing loaded means nothing is late, so []. Also the input for the months.missing event (issue #35)."""
    expected, out, m = expected_month(on or today()), [], max(months, default=None)
    while m and m < expected:
        m = f"{int(m[:4]) + int(m[5:7]) // 12}-{int(m[5:7]) % 12 + 1:02d}"
        out.append(m)
    return out


def stale_status(months: list[str], on: _dt.date | None = None) -> dict:
    on = on or today()
    missing = missing_months(months, on)
    names = [_NAMES[int(x[5:7]) - 1] for x in missing]
    msg = None if not names else f"{names[0]}'s exports are not loaded yet" if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}: exports are not loaded yet"
    return {"stale": bool(missing), "expected_month": expected_month(on), "latest_month": max(months, default=None), "months_missing": missing, "message": msg}
