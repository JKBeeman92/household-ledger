"""The optional compose `backup` service: its YAML, and the dump script it runs (executed here with sh, against the test Postgres)."""
from __future__ import annotations
import glob, os, shutil, subprocess, time, uuid
from pathlib import Path

import psycopg, pytest, yaml
from psycopg.conninfo import conninfo_to_dict, make_conninfo

COMPOSE = Path(__file__).parent.parent / "docker-compose.yml"


def service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["backup"]


def script(backups: Path) -> str:
    return service()["command"][0].replace("$$", "$").replace("/backups", str(backups))


def pg_bin(name: str) -> str:
    found = shutil.which(name) or next(iter(sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"))), None)
    if not found:
        pytest.skip(f"{name} not installed")
    return found


def test_backup_service_is_opt_in_and_persists_outside_the_data_dir():
    svc, doc = service(), yaml.safe_load(COMPOSE.read_text())
    assert svc["profiles"] == ["backup"]
    assert "profiles" not in doc["services"]["ledger"] and "profiles" not in doc["services"]["ledger-db"]
    assert svc["image"].startswith("postgres:16") and svc["depends_on"]["ledger-db"]["condition"] == "service_healthy"
    assert svc["volumes"] == ["${LEDGER_DATA:-./data}/backups:/backups"]
    assert svc["environment"]["PGHOST"] == "ledger-db" and svc["environment"]["BACKUP_KEEP_DAYS"] == "${BACKUP_KEEP_DAYS:-14}"
    assert svc["entrypoint"] == ["sh", "-c"] and "$$(date" in svc["command"][0]


@pytest.fixture
def two_dbs():
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set")
    names = ["bak_" + uuid.uuid4().hex[:8] for _ in range(2)]
    with psycopg.connect(base, autocommit=True) as admin:
        for n in names:
            admin.execute(f'CREATE DATABASE "{n}"')
    yield [make_conninfo(base, dbname=n) for n in names]
    with psycopg.connect(base, autocommit=True) as admin:
        for n in names:
            admin.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')


def pgenv(url: str, **extra) -> dict:
    p = conninfo_to_dict(url)
    env = {**os.environ, "PGHOST": p.get("host", "127.0.0.1"), "PGPORT": str(p.get("port", 5432)), "PGUSER": p.get("user", "ledger"),
           "PGDATABASE": p["dbname"], "BACKUP_KEEP_DAYS": "14", "BACKUP_INTERVAL_HOURS": "24", "BACKUP_ONCE": "1", **extra}
    env["PATH"] = f"{Path(pg_bin('pg_dump')).parent}:{env['PATH']}"
    env.pop("PGPASSWORD", None)
    if p.get("password"):   # a password-authenticated test server (CI); trust auth needs none
        env["PGPASSWORD"] = p["password"]
    return env


def test_dump_script_writes_restorable_dump_and_prunes_old_ones(two_dbs, tmp_path):
    src, dst = two_dbs
    with psycopg.connect(src, autocommit=True) as c:
        c.execute("CREATE TABLE goals_t (k text, v numeric)")
        c.execute("INSERT INTO goals_t VALUES ('Bywater cottage fund', 1234.50)")
    old = tmp_path / "ledger-20200101T000000Z.dump"
    old.write_text("stale")
    stale_tmp = tmp_path / "ledger-20200101T000001Z.dump.tmp"
    stale_tmp.write_text("partial")
    keep = tmp_path / "notes.txt"
    keep.write_text("not a dump")
    past = time.time() - 40 * 86400
    for f in (old, stale_tmp, keep):
        os.utime(f, (past, past))

    r = subprocess.run(["sh", "-c", script(tmp_path)], env=pgenv(src), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    dumps = sorted(tmp_path.glob("ledger-*.dump"))
    assert len(dumps) == 1 and "backup written" in r.stdout, r.stderr
    assert not old.exists() and not stale_tmp.exists() and keep.exists()
    assert (dumps[0].stat().st_mode & 0o077) == 0

    restore = subprocess.run([pg_bin("pg_restore"), "--no-owner", "-d", dst, str(dumps[0])], capture_output=True, text=True, timeout=60)
    assert restore.returncode == 0, restore.stderr
    with psycopg.connect(dst) as c:
        assert c.execute("SELECT k, v::text FROM goals_t").fetchall() == [("Bywater cottage fund", "1234.50")]


def test_failed_dump_leaves_no_dump_file_and_keeps_earlier_ones(tmp_path):
    good = tmp_path / "ledger-20260101T000000Z.dump"
    good.write_text("earlier good dump")
    env = pgenv("postgresql://ledger@127.0.0.1:1/nope")
    r = subprocess.run(["sh", "-c", script(tmp_path)], env=env, capture_output=True, text=True, timeout=60)
    assert "backup FAILED" in r.stderr
    assert sorted(p.name for p in tmp_path.glob("ledger-*.dump")) == [good.name] and good.read_text() == "earlier good dump"
