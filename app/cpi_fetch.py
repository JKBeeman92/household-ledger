"""Fetch the CPI series (FRED CSV format) and merge it into the `cpi` table: append missing months, replace estimated ones."""
from __future__ import annotations
import csv, io, re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
SETTING = "cpi_series_url"
MAX_BYTES = 1_000_000
ESTIMATE_RE = re.compile(r"carried|interpolat|estimat", re.I)
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class CpiFetchError(ValueError):
    pass


def is_estimate(source: str) -> bool:
    return bool(ESTIMATE_RE.search(source or ""))


def _http_get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "household-ledger"})
    with urlopen(req, timeout=20) as r:  # noqa: S310 (scheme checked in check_url)
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise CpiFetchError("response is too large to be a CPI series")
    return data.decode("utf-8-sig")


TRANSPORT = _http_get  # url -> response text; tests replace this so nothing touches the network


def check_url(url: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise CpiFetchError("the series URL must start with http:// or https://")
    return url


def series_url(conn) -> str:
    from . import db
    return (db.get_setting(conn, SETTING) or DEFAULT_URL)


def source_label(url: str) -> str:
    return "FRED CPIAUCSL" if url == DEFAULT_URL else f"FRED-format series ({urlparse(url).netloc})"


def parse(text: str) -> dict[str, float]:
    """FRED CSV: a header line, then `date,value` rows with dates like 2026-09-01; '.' marks an unpublished month. All or nothing."""
    rows = list(csv.reader(io.StringIO(text.strip())))
    if len(rows) < 2 or len(rows[0]) != 2 or rows[0][0].strip().lower() not in ("date", "observation_date", "month"):
        raise CpiFetchError("not a CPI series: expected a header like observation_date,CPIAUCSL")
    out: dict[str, float] = {}
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != 2:
            raise CpiFetchError(f"line {n}: expected date,value")
        month, raw = r[0].strip()[:7], r[1].strip()
        if not MONTH_RE.match(month):
            raise CpiFetchError(f"line {n}: bad date '{r[0]}'")
        if raw in (".", ""):
            continue
        try:
            v = float(raw)
        except ValueError:
            raise CpiFetchError(f"line {n}: bad value '{raw}'") from None
        if not 0 < v < 1e6 or v != v:
            raise CpiFetchError(f"line {n}: implausible value '{raw}'")
        out[month] = v
    if not out:
        raise CpiFetchError("the series has no published values")
    return out


def fetch_latest(conn, url: str | None = None) -> dict:
    """Returns {"added": [...], "replaced": [...]}. Raises CpiFetchError before touching the table; the merge is one transaction (caller commits)."""
    url = check_url(url or series_url(conn))
    try:
        text = TRANSPORT(url)
    except CpiFetchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise CpiFetchError(f"could not fetch {url}: {e}") from e
    fetched = parse(text)
    have = {r["month"]: r["source"] for r in conn.execute("SELECT month, source FROM cpi").fetchall()}
    label = source_label(url)
    added = sorted(m for m in fetched if m not in have)
    replaced = sorted(m for m in fetched if m in have and is_estimate(have[m]))
    cur = conn.cursor()
    cur.executemany("INSERT INTO cpi (month, cpi, source) VALUES (%s,%s,%s) ON CONFLICT (month) DO UPDATE SET cpi=EXCLUDED.cpi, source=EXCLUDED.source",
                    [(m, f"{fetched[m]:.3f}", label) for m in added + replaced])
    return {"added": added, "replaced": replaced}
