"""Rebuilds run in the background, only one at a time (across processes), and a crashed build never blocks the next one."""
from __future__ import annotations
import dataclasses, shutil

import psycopg
import pytest

from conftest import GOLDEN, upload_files
from pipeline.run_pipeline import BUILD_LOCK, BuildBusy, rebuild


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    return client


@pytest.fixture
def other_session():
    """A second database session, standing in for another process, that can hold the build lock."""
    from app.config import settings
    conn = psycopg.connect(settings.database_url, autocommit=True)
    yield conn
    conn.close()


def hold_lock(conn):
    assert conn.execute("SELECT pg_try_advisory_lock(%s, %s)", BUILD_LOCK).fetchone()[0]


def test_upload_rebuilds_in_the_background_and_reports_status(loaded):
    st = loaded.get("/api/build").json()
    assert st["state"] == "ok" and st["running"] is False and "ledger rows" in st["summary"]
    page = loaded.get("/upload").text
    assert "Last build finished" in page and 'data-state="ok"' in page


def test_post_rebuild_redirects_before_the_page_shows_the_result(loaded):
    r = loaded.post("/rebuild")
    assert r.status_code == 303 and "Rebuilding in the background" in loaded.get(r.headers["location"]).text


def test_second_rebuild_is_refused_while_another_session_holds_the_lock(loaded, other_session):
    from app import db
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO build_log DEFAULT VALUES"); conn.commit()
        before = conn.execute("SELECT count(*) n FROM build_log").fetchone()["n"]
    hold_lock(other_session)
    assert loaded.get("/api/build").json()["state"] == "running"
    r = loaded.post("/rebuild")
    assert "already running" in loaded.get(r.headers["location"]).text
    page = loaded.get("/upload").text
    assert 'data-state="running"' in page and "Rebuilding" in page and "disabled" in page
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM build_log").fetchone()["n"] == before
        with pytest.raises(BuildBusy):
            rebuild(conn)
    # an upload while busy still stores the files and says the rebuild was not started
    r = loaded.post("/upload", files=[("files", (p.name, p.read_bytes(), "text/csv")) for p in (GOLDEN / "exports").glob("c0ffee-01-*.csv")])
    assert "already running" in loaded.get(r.headers["location"]).text


def test_stale_running_row_does_not_block_and_is_marked_interrupted(loaded):
    from app import db
    with db.pool.connection() as conn:
        stale = conn.execute("INSERT INTO build_log DEFAULT VALUES RETURNING id").fetchone()["id"]; conn.commit()
    st = loaded.get("/api/build").json()   # no lock holder: the crashed process is gone
    assert st["state"] == "interrupted" and st["running"] is False
    assert "Last build was interrupted" in loaded.get("/upload").text
    loaded.post("/rebuild")
    with db.pool.connection() as conn:
        row = conn.execute("SELECT ok, finished_at, summary FROM build_log WHERE id=%s", (stale,)).fetchone()
    assert row["ok"] is False and row["finished_at"] is not None and "interrupted" in row["summary"]
    assert loaded.get("/api/build").json()["state"] == "ok"


def test_lock_is_released_after_a_failed_rebuild(loaded, monkeypatch):
    from app import db
    from pipeline import run_pipeline
    monkeypatch.setattr(run_pipeline, "run_scripts", lambda wd: (_ for _ in ()).throw(RuntimeError("boom")))
    with db.pool.connection() as conn:
        with pytest.raises(RuntimeError):
            rebuild(conn)
        assert run_pipeline.build_running(conn) is False
    assert loaded.get("/api/build").json()["state"] == "failed"


def test_build_api_requires_sign_in(client, reset_db, monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))
    assert client.get("/api/build").status_code == 401


def test_failed_startup_import_is_logged_and_recorded_not_raised(client, reset_db, tmp_path, monkeypatch):
    from app import db
    d = tmp_path / "imp"; d.mkdir()
    for f in (GOLDEN / "exports").glob("c0ffee-01-*.csv"):
        text = f.read_text().replace("Rent", "Elevenses Fund")   # an item the mapping has never seen
        (d / f.name).write_text(text)
    monkeypatch.setattr(db, "settings", dataclasses.replace(db.settings, import_dir=str(d)))
    db.init()   # must not raise
    with db.pool.connection() as conn:
        row = conn.execute("SELECT ok, summary FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["ok"] is False and row["summary"]


def test_startup_import_failure_before_rebuild_is_recorded(client, reset_db, tmp_path, monkeypatch):
    from app import db
    d = tmp_path / "imp"; d.mkdir()
    shutil.copy(next((GOLDEN / "exports").glob("c0ffee-01-*Transactions.csv")), d / "c0ffee-01-2025-EveryDollar-Transactions.csv")
    monkeypatch.setattr(db, "import_dir", lambda conn, p: (_ for _ in ()).throw(ValueError("unreadable export")))
    monkeypatch.setattr(db, "settings", dataclasses.replace(db.settings, import_dir=str(d)))
    db.init()
    with db.pool.connection() as conn:
        row = conn.execute("SELECT ok, summary FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["ok"] is False and "start-up import failed" in row["summary"] and "unreadable export" in row["summary"]


def test_a_lock_held_in_another_database_is_not_our_build(loaded):
    """Advisory locks are per database: a rebuild of a different database on the same server must not read as ours."""
    from app.config import settings
    name = "lock_" + __import__("uuid").uuid4().hex[:8]
    with psycopg.connect(settings.database_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        with psycopg.connect(settings.database_url, dbname=name, autocommit=True) as elsewhere:
            hold_lock(elsewhere)
            assert loaded.get("/api/build").json()["running"] is False
    finally:
        with psycopg.connect(settings.database_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_status_page_renders_while_locked_before_any_build_row(client, reset_db, other_session):
    hold_lock(other_session)
    assert client.get("/upload").status_code == 200 and client.get("/api/build").json()["state"] == "running"


def test_a_month_with_one_file_names_the_missing_file(client, reset_db, tmp_path):
    """Half a month reconciles as every envelope wrong; the build summary leads with which file is missing."""
    from pipeline.run_pipeline import rebuild
    from app import db
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        m = conn.execute("SELECT max(budget_month) m FROM source_files").fetchone()["m"]
        conn.execute("DELETE FROM source_files WHERE budget_month=%s AND kind='budget_items'", (m,)); conn.commit()
        res = rebuild(conn)   # the other months still build (README: a month that fails still loads, and the page says so)
    assert res["summary"].startswith(f"{m}: the BudgetItems file is missing (upload it to finish this month). ")
