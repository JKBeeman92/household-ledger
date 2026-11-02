"""Connection pool, schema application and first-boot seeding."""
from __future__ import annotations
import json, logging
from pathlib import Path
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from .config import settings
from pipeline.run_pipeline import seed_reference_tables, import_dir, HERE as PIPELINE_DIR

log = logging.getLogger("ledger")
ROOT = Path(__file__).resolve().parent.parent
pool = ConnectionPool(settings.database_url, min_size=1, max_size=6, open=False, kwargs={"row_factory": dict_row})


def init() -> None:
    pool.open()
    with pool.connection() as conn:
        conn.execute((ROOT / "db" / "schema.sql").read_text())
        seed_dir = Path(settings.seed_dir) if settings.seed_dir else PIPELINE_DIR
        seed_reference_tables(conn, seed_dir)
        if conn.execute("SELECT 1 FROM settings WHERE key='household'").fetchone() is None:
            hh = {"name": "Household", "title": "Household Ledger", "theme": "beeman", "currency": "USD"}
            p = seed_dir / "household.json"
            if not p.exists():
                p = PIPELINE_DIR / "household.json"
            if p.exists():
                hh.update(json.loads(p.read_text()))
            conn.execute("INSERT INTO settings (key, value) VALUES ('household', %s)", (json.dumps(hh),))
        conn.commit()
        if settings.import_dir:
            res = import_dir(conn, Path(settings.import_dir))
            log.info("import dir %s: %s", settings.import_dir, res)


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    return row["value"] if row else default


def put_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, json.dumps(value)))
