"""Export downloads and the import-directory scan."""
from __future__ import annotations
import csv, dataclasses, io, json, shutil, zipfile

import pytest

from conftest import GOLDEN, upload_files


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")


@pytest.fixture
def auth_on(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))


def test_ledger_csv_matches_the_db(client, loaded):
    r = client.get("/export/ledger.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "ledger.csv" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    from app import db
    with db.pool.connection() as conn:
        n = conn.execute("SELECT count(*) c FROM ledger").fetchone()["c"]
    assert len(rows) == n > 0
    assert list(rows[0]) [:3] == ["BudgetMonth", "CalendarMonth", "Date"] and rows[0]["IsTransfer"] in ("0", "1")
    assert rows[0]["BudgetMonth"] and rows[0]["Amount"]
    assert [r["BudgetMonth"] for r in rows] == sorted(r["BudgetMonth"] for r in rows)


def test_datasets_zip_has_every_dataset(client, loaded):
    r = client.get("/export/datasets.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content)); assert z.testzip() is None
    api = client.get("/api/datasets").json()
    assert {n[:-5] for n in z.namelist()} == set(api)
    for name, rows in api.items():
        assert json.loads(z.read(f"{name}.json")) == rows


def test_sources_zip_round_trips_the_raw_exports(client, loaded):
    r = client.get("/export/sources.zip")
    z = zipfile.ZipFile(io.BytesIO(r.content)); assert z.testzip() is None
    originals = {p.name: p for p in (GOLDEN / "exports").glob("*.csv")}
    assert set(z.namelist()) == set(originals)
    for name, p in originals.items():
        want = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"))))
        got = list(csv.reader(io.StringIO(z.read(name).decode())))
        assert got[0] == want[0] or set(got[0]) == set(want[0])
        assert len(got) == len(want), name
    # and they re-ingest cleanly into an empty ledger, with the same reconciliation
    from pipeline.run_pipeline import parse_export
    for name in z.namelist():
        parse_export(name, z.read(name))


def test_empty_database_exports_are_valid(client, reset_db):
    assert client.get("/export/ledger.csv").text.startswith("BudgetMonth,")
    assert zipfile.ZipFile(io.BytesIO(client.get("/export/sources.zip").content)).namelist() == []
    assert zipfile.ZipFile(io.BytesIO(client.get("/export/datasets.zip").content)).namelist() == []


def test_exports_require_sign_in(client, loaded, auth_on):
    for path in ("/export/ledger.csv", "/export/datasets.zip", "/export/sources.zip"):
        assert client.get(path).status_code == 307
    r = client.post("/import/scan")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")   # 303: the browser must not re-POST to /login


def test_scan_import_dir(client, reset_db, monkeypatch, tmp_path):
    from app.routes import export
    for p in sorted((GOLDEN / "exports").glob("*.csv")):
        shutil.copy(p, tmp_path / p.name)
    monkeypatch.setattr(export, "settings", dataclasses.replace(export.settings, import_dir=str(tmp_path)))
    r = client.post("/import/scan")
    assert r.status_code == 303 and r.headers["location"] == "/upload"
    assert "8 new or changed file(s) loaded" in client.get("/upload").text
    r = client.post("/import/scan")
    assert "0 new or changed file(s) loaded, 8 unchanged" in client.get(r.headers["location"]).text


def test_scan_without_import_dir_says_so(client, reset_db):
    r = client.post("/import/scan")
    assert r.status_code == 303 and "IMPORT_DIR is not set" in client.get("/upload").text


def test_upload_page_has_the_scan_form(client, reset_db):
    t = client.get("/upload").text
    assert 'action="/import/scan"' in t and "/export/sources.zip" in t


def test_scan_survives_one_bad_file_and_says_which(client, reset_db, monkeypatch, tmp_path):
    from app.routes import export
    from app import db
    for p in sorted((GOLDEN / "exports").glob("*.csv")):
        text = p.read_text()
        if p.name == "c0ffee-02-2025-EveryDollar-Transactions.csv":
            text = text.replace("02/03/2025", "2025-02-03")
        (tmp_path / p.name).write_text(text)
    monkeypatch.setattr(export, "settings", dataclasses.replace(export.settings, import_dir=str(tmp_path)))
    r = client.post("/import/scan")
    page = client.get(r.headers["location"]).text
    assert "7 new or changed file(s) loaded" in page and "Rebuilt:" in page
    assert "Not loaded: c0ffee-02-2025-EveryDollar-Transactions.csv" in page
    with db.pool.connection() as conn:
        months = {x["budget_month"] for x in conn.execute("SELECT budget_month FROM ledger").fetchall()}
        loaded = conn.execute("SELECT count(*) n FROM source_files").fetchone()["n"]
        last = conn.execute("SELECT ok, summary, detail FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    assert loaded == 7 and {"2025-01", "2025-03", "2025-04"} <= months
    assert last["ok"] is False and "c0ffee-02-2025-EveryDollar-Transactions.csv" in last["summary"] and "c0ffee-02-2025-EveryDollar-Transactions.csv" in last["detail"]
