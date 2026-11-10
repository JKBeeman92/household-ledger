"""Connection pool, schema application and first-boot seeding."""
from __future__ import annotations
import json, logging
from pathlib import Path
import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from .config import settings
from pipeline.run_pipeline import seed_reference_tables, import_dir, BuildBusy, HERE as PIPELINE_DIR

log = logging.getLogger("ledger")
ROOT = Path(__file__).resolve().parent.parent
pool = ConnectionPool(settings.database_url, min_size=1, max_size=6, open=False, kwargs={"row_factory": dict_row})


MIGRATIONS = ROOT / "db" / "migrations"
LOCK_KEY = 0x6C6564676572  # "ledger": advisory lock so concurrently starting processes migrate one at a time


class MigrationError(RuntimeError):
    pass


def migrate(url: str, directory: Path = MIGRATIONS) -> list[str]:
    """Apply every db/migrations/*.sql not yet recorded in schema_migrations, in filename order, one transaction each.
    Tracks the applied set (not a max version) so out-of-order numbers from parallel branches still apply."""
    files = sorted(directory.glob("*.sql"))
    done: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
            for f in files:
                if f.name in applied:
                    continue
                try:
                    with conn.transaction():
                        conn.execute(f.read_text())
                        conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (f.name,))
                except Exception as e:
                    raise MigrationError(f"migration {f.name} failed and was rolled back: {e}") from e
                log.info("applied migration %s", f.name)
                done.append(f.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return done


def init() -> None:
    migrate(settings.database_url)
    pool.open()
    with pool.connection() as conn:
        seed_dir = Path(settings.seed_dir) if settings.seed_dir else PIPELINE_DIR
        seed_reference_tables(conn, seed_dir)
        if conn.execute("SELECT 1 FROM settings WHERE key='household'").fetchone() is None:
            hh = {"name": "Household", "title": "Household Ledger", "theme": "homestead", "currency": "USD", "locale": "en-US"}
            p = seed_dir / "household.json"
            if not p.exists():
                p = PIPELINE_DIR / "household.json"
            if p.exists():
                hh.update(json.loads(p.read_text()))
            conn.execute("INSERT INTO settings (key, value) VALUES ('household', %s)", (json.dumps(hh),))
        conn.commit()
        if settings.import_dir:
            last_build_id = conn.execute("SELECT coalesce(max(id), 0) m FROM build_log").fetchone()["m"]
            try:
                res = import_dir(conn, Path(settings.import_dir))
                log.info("import dir %s: %s", settings.import_dir, res)
            except BuildBusy:
                log.warning("import dir %s: another rebuild is running; start-up import skipped", settings.import_dir)
            except Exception as e:  # noqa: BLE001  a bad import must never stop the app from starting
                log.error("import dir %s failed: %s", settings.import_dir, str(e)[:800])
                try:
                    conn.rollback()
                    if conn.execute("SELECT 1 FROM build_log WHERE id > %s", (last_build_id,)).fetchone() is None:  # rebuild() records its own failures
                        conn.execute("INSERT INTO build_log (finished_at, ok, summary, detail) VALUES (now(), false, %s, %s)", (f"start-up import failed: {str(e)[:450]}", str(e)))
                    conn.commit()
                except Exception:  # noqa: BLE001
                    log.exception("could not record the failed start-up import in build_log")


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    return row["value"] if row else default


def put_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, json.dumps(value)))
