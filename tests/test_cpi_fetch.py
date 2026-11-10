"""CPI fetch: stubbed transport only (never the network); a failed or malformed fetch changes nothing."""
from __future__ import annotations
import dataclasses

import pytest

from app import cpi_fetch, db

GOOD = "observation_date,CPIAUCSL\n2025-03-01,320.000\n2025-04-01,321.500\n2025-05-01,.\n2025-06-01,322.250\n"


@pytest.fixture
def stub(monkeypatch):
    calls = []
    def install(text=None, exc=None):
        def transport(url):
            calls.append(url)
            if exc:
                raise exc
            return text
        monkeypatch.setattr(cpi_fetch, "TRANSPORT", transport)
        return calls
    return install


@pytest.fixture
def auth_on(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))


def table(conn):
    return {r["month"]: (float(r["cpi"]), r["source"]) for r in conn.execute("SELECT month, cpi, source FROM cpi").fetchall()}


def seed(conn, rows):
    conn.execute("DELETE FROM cpi")
    conn.cursor().executemany("INSERT INTO cpi VALUES (%s,%s,%s)", rows); conn.commit()


def test_parse_skips_unpublished_and_takes_months():
    assert cpi_fetch.parse(GOOD) == {"2025-03": 320.0, "2025-04": 321.5, "2025-06": 322.25}


@pytest.mark.parametrize("bad", ["", "<html>nope</html>", "observation_date,CPIAUCSL\n", "observation_date,CPIAUCSL\n2025-03-01,abc\n",
                                 "observation_date,CPIAUCSL\nyesterday,300\n", "observation_date,CPIAUCSL\n2025-03-01,300,9\n", "observation_date,CPIAUCSL\n2025-03-01,-4\n",
                                 "observation_date,CPIAUCSL\n2025-03-01,.\n"])
def test_parse_rejects_malformed(bad):
    with pytest.raises(cpi_fetch.CpiFetchError):
        cpi_fetch.parse(bad)


def test_fetch_appends_missing_and_replaces_estimates_only(client, reset_db, stub):
    calls = stub(GOOD)
    with db.pool.connection() as conn:
        seed(conn, [("2025-03", "319.000", "FRED CPIAUCSL"), ("2025-04", "320.000", "CARRIED FORWARD (not yet published)"), ("2025-05", "321.000", "INTERPOLATED (gap)")])
        res = cpi_fetch.fetch_latest(conn); conn.commit()
        t = table(conn)
    assert calls == [cpi_fetch.DEFAULT_URL]
    assert res == {"added": ["2025-06"], "replaced": ["2025-04"]}
    assert t["2025-03"] == (319.0, "FRED CPIAUCSL")                 # published rows are never touched, even if the feed differs
    assert t["2025-04"] == (321.5, "FRED CPIAUCSL")                 # estimate replaced
    assert t["2025-05"] == (321.0, "INTERPOLATED (gap)")            # feed has no value yet: estimate stays
    assert t["2025-06"] == (322.25, "FRED CPIAUCSL")


@pytest.mark.parametrize("text,exc", [("garbage", None), ("observation_date,CPIAUCSL\n2025-06-01,300\n2025-07-01,x\n", None), (None, OSError("offline"))])
def test_failed_or_malformed_fetch_changes_nothing(client, reset_db, stub, text, exc):
    stub(text, exc)
    with db.pool.connection() as conn:
        seed(conn, [("2025-04", "320.000", "ESTIMATE (not yet published)")])
        before = table(conn)
        with pytest.raises(cpi_fetch.CpiFetchError):
            cpi_fetch.fetch_latest(conn)
        conn.rollback()
        assert table(conn) == before


def test_url_setting_and_validation(client, reset_db, stub):
    calls = stub(GOOD)
    assert client.post("/cpi/url", data={"url": "ftp://x/y.csv"}).status_code == 303
    with db.pool.connection() as conn:
        assert db.get_setting(conn, cpi_fetch.SETTING) is None
    client.post("/cpi/url", data={"url": "https://stats.example.org/cpi.csv"})
    client.post("/cpi/fetch")
    assert calls == ["https://stats.example.org/cpi.csv"]
    with db.pool.connection() as conn:
        assert table(conn)["2025-06"][1] == "FRED-format series (stats.example.org)"
    client.post("/cpi/url", data={"url": ""})
    with db.pool.connection() as conn:
        assert cpi_fetch.series_url(conn) == cpi_fetch.DEFAULT_URL


def test_fetch_route_reports_and_rebuilds(client, reset_db, stub):
    stub(GOOD)
    with db.pool.connection() as conn:
        seed(conn, [("2025-04", "320.000", "ESTIMATE (not yet published)")])
    r = client.post("/cpi/fetch")
    assert r.status_code == 303 and r.headers["location"] == "/cpi"
    assert "Added 2 month(s), replaced 1 estimated" in client.get("/cpi").text
    stub("not a series")
    client.post("/cpi/fetch")
    assert "Nothing changed" in client.get("/cpi").text


def test_cpi_routes_need_editor(client, reset_db, auth_on):
    assert client.get("/cpi").status_code in (303, 307, 401, 403)
    assert client.post("/cpi/fetch").status_code in (303, 307, 401, 403)


def test_method_tab_block_and_settings_link(client, reset_db):
    page = client.get("/").text
    assert 'id="cpi-estimated"' in page and "dash.draw('cpi-estimated'" in page
    assert 'href="/cpi"' in client.get("/settings").text
