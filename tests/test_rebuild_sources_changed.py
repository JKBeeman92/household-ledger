"""A change to source_files while a rebuild runs must end up in the ledger: the build runs again (bounded). The interleaving is injected, no sleeps."""
from __future__ import annotations
import psycopg
import pytest

from conftest import GOLDEN
from pipeline import run_pipeline
from pipeline.run_pipeline import ingest, rebuild

EXPORTS = GOLDEN / "exports"


def load_months(conn, months):
    for m in months:
        for p in sorted(EXPORTS.glob(f"c0ffee-{m}-*.csv")):
            ingest(conn, p.name, p.read_bytes(), uploaded_by="test")


def ledger_months(conn):
    return sorted({r["budget_month"] for r in conn.execute("SELECT budget_month FROM ledger").fetchall()})


def hook_scripts(monkeypatch, during):
    """Call `during(n)` (n = pass number from 1) in the middle of each pass, after the files were exported and before the ledger is loaded."""
    real, calls = run_pipeline.run_scripts, []

    def run_scripts(wd):
        calls.append(1); during(len(calls))
        return real(wd)
    monkeypatch.setattr(run_pipeline, "run_scripts", run_scripts)
    return calls


def other_conn():
    from app.config import settings
    return psycopg.connect(settings.database_url, autocommit=False)


def test_upload_during_a_build_is_picked_up_by_a_second_pass(client, reset_db, monkeypatch):
    from app import db
    with db.pool.connection() as conn:
        load_months(conn, ["01", "02"])

    def during(n):
        if n == 1:
            with other_conn() as c:
                load_months(c, ["03"])
    calls = hook_scripts(monkeypatch, during)
    with db.pool.connection() as conn:
        res = rebuild(conn)
        assert len(calls) == 2 and "changed again" not in res["summary"]
        assert ledger_months(conn) == ["2025-01", "2025-02", "2025-03"]
        assert conn.execute("SELECT count(*) n FROM build_log WHERE ok").fetchone()["n"] == 2


def test_delete_during_a_build_is_picked_up_too(client, reset_db, monkeypatch):
    from app import db
    with db.pool.connection() as conn:
        load_months(conn, ["01", "02"])

    def during(n):
        if n == 1:
            with other_conn() as c:
                c.execute("DELETE FROM source_files WHERE budget_month='2025-02'"); c.commit()
    hook_scripts(monkeypatch, during)
    with db.pool.connection() as conn:
        rebuild(conn)
        assert ledger_months(conn) == ["2025-01"]


def test_the_queued_task_that_was_refused_does_not_lose_the_files(client, reset_db, monkeypatch):
    """Two requests both saw no build running; the second one's task hits the lock and is dropped. Its files must still be built."""
    from app import db
    from app.builds import run_background
    with db.pool.connection() as conn:
        load_months(conn, ["01"])

    def during(n):
        if n == 1:
            with other_conn() as c:
                load_months(c, ["02"])
            run_background()   # the second request's task: BuildBusy, swallowed
    hook_scripts(monkeypatch, during)
    with db.pool.connection() as conn:
        rebuild(conn)
        assert ledger_months(conn) == ["2025-01", "2025-02"]


def test_a_source_that_never_stops_changing_still_ends(client, reset_db, monkeypatch):
    from app import db
    with db.pool.connection() as conn:
        load_months(conn, ["01"])

    def during(n):
        with other_conn() as c:
            c.execute("UPDATE source_files SET sha256 = sha256 || 'x' WHERE id = (SELECT min(id) FROM source_files)"); c.commit()
    calls = hook_scripts(monkeypatch, during)
    with db.pool.connection() as conn:
        res = rebuild(conn)
        last = conn.execute("SELECT summary FROM build_log ORDER BY id DESC LIMIT 1").fetchone()["summary"]
    assert len(calls) == run_pipeline.MAX_PASSES and "changed again" in res["summary"] and "changed again" in last


def test_an_unchanged_build_runs_once(client, reset_db, monkeypatch):
    from app import db
    with db.pool.connection() as conn:
        load_months(conn, ["01"])
    calls = hook_scripts(monkeypatch, lambda n: None)
    with db.pool.connection() as conn:
        rebuild(conn)
    assert len(calls) == 1
