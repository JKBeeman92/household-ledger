"""Migration runner tests. Each test gets a scratch database created next to DATABASE_URL's and dropped afterwards."""
from __future__ import annotations
import os, uuid
from pathlib import Path

import psycopg, pytest
from psycopg.conninfo import make_conninfo

from app import db

LEGACY = Path(__file__).parent / "fixtures" / "legacy_schema.sql"
TABLES = {"source_files", "transactions", "budget_items", "item_mapping", "cpi", "goals", "life_events", "ledger", "datasets", "reviews", "build_log", "settings"}


@pytest.fixture
def scratch():
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set")
    name = "mig_" + uuid.uuid4().hex[:10]
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_conninfo(base, dbname=name)
    yield url
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def q(url, sql):
    with psycopg.connect(url, autocommit=True) as c:
        return c.execute(sql).fetchall()


def test_fresh_database_applies_everything(scratch):
    assert db.migrate(scratch) == [p.name for p in sorted(db.MIGRATIONS.glob("*.sql"))]
    assert TABLES <= {r[0] for r in q(scratch, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert q(scratch, "SELECT filename FROM schema_migrations")[0][0] == "0001_baseline.sql"


def test_existing_database_adopts_baseline_and_keeps_rows(scratch):
    with psycopg.connect(scratch, autocommit=True) as c:
        c.execute(LEGACY.read_text())
        c.execute("INSERT INTO source_files (kind, budget_month, filename, sha256, row_count) VALUES ('transactions','2025-01','shire.csv','abc',1)")
        c.execute("INSERT INTO transactions (source_file_id, budget_month, row_no, grp, item, type, date, merchant, amount) VALUES (1,'2025-01',1,'Food','Groceries','expense','2025-01-05','Bywater Market',12.50)")
        c.execute("INSERT INTO item_mapping (item, canonical_item, category, flow) VALUES ('groceries','Groceries','Food','expense')")
    assert db.migrate(scratch) == [p.name for p in sorted(db.MIGRATIONS.glob("*.sql"))]
    assert q(scratch, "SELECT merchant, amount::text FROM transactions") == [("Bywater Market", "12.50")]
    assert q(scratch, "SELECT item FROM item_mapping") == [("groceries",)]
    assert q(scratch, "SELECT count(*) FROM source_files") == [(1,)]


def test_second_start_applies_nothing(scratch):
    db.migrate(scratch)
    assert db.migrate(scratch) == []


def test_out_of_order_migration_is_applied(scratch, tmp_path):
    for p in db.MIGRATIONS.glob("0001_*.sql"):   # only the baseline, so later real migrations do not change the expected lists
        (tmp_path / p.name).write_text(p.read_text())
    (tmp_path / "0022_second_breakfast.sql").write_text("CREATE TABLE second_breakfast (id INT);")
    assert db.migrate(scratch, tmp_path) == sorted(p.name for p in tmp_path.glob("*.sql"))
    (tmp_path / "0010_elevenses.sql").write_text("CREATE TABLE elevenses (id INT);")
    assert db.migrate(scratch, tmp_path) == ["0010_elevenses.sql"]
    assert db.migrate(scratch, tmp_path) == []
    assert {r[0] for r in q(scratch, "SELECT filename FROM schema_migrations")} == {p.name for p in tmp_path.glob("*.sql")}


def test_broken_migration_fails_loudly_and_is_not_recorded(scratch, tmp_path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE ok_table (id INT);")
    (tmp_path / "0002_broken.sql").write_text("CREATE TABLE half_done (id INT); SELECT * FROM no_such_table;")
    with pytest.raises(db.MigrationError, match="0002_broken.sql"):
        db.migrate(scratch, tmp_path)
    assert q(scratch, "SELECT filename FROM schema_migrations") == [("0001_ok.sql",)]
    assert q(scratch, "SELECT to_regclass('half_done')") == [(None,)]
    assert q(scratch, "SELECT pg_try_advisory_lock(%d)" % db.LOCK_KEY) == [(True,)]
