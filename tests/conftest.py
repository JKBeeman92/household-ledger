"""Shared fixtures. app/config.py reads the environment at import, so it is set here before anything imports the app.

Database tests need DATABASE_URL (any empty Postgres database you own; every table the app uses is truncated in it) and are
skipped without it: the default local URL is never touched."""
from __future__ import annotations
import os
from pathlib import Path

import pytest

os.environ.update(AUTH_DISABLED="true", SESSION_SECRET="test-session-secret-0123456789abcdef0123", BASE_URL="http://testserver")
for _k in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "ANTHROPIC_API_KEY", "SEED_DIR", "IMPORT_DIR"):
    os.environ[_k] = ""

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
RECON_FAIL = ROOT / "tests" / "fixtures" / "recon_fail"
SAMPLE = ROOT / "examples" / "sample-household"
TABLES = "source_files, transactions, budget_items, item_mapping, cpi, goals, life_events, ledger, datasets, reviews, build_log, settings"


@pytest.fixture(scope="session")
def client():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set: database tests need a Postgres database to reset")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, follow_redirects=False) as c:
        yield c
    from app import db
    db.pool.close()


@pytest.fixture
def reset_db(client):
    """Return reset(refs=GOLDEN): empty every table in DATABASE_URL, then seed the reference tables from `refs`. Called once already."""
    from app import db
    from pipeline.run_pipeline import seed_reference_tables

    def reset(refs: Path = GOLDEN) -> None:
        with db.pool.connection() as conn:
            conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
            seed_reference_tables(conn, refs)
            db.put_setting(conn, "household", {"name": "Household", "title": "Household Ledger", "theme": "beeman", "currency": "USD"})
    reset()
    return reset


def upload_files(client, directory: Path):
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(directory.glob("*.csv"))]
    return client.post("/upload", files=files)
