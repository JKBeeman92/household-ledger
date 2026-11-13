"""/export/settings.zip and `app.admin export-settings` (#160): every setting in the seed-directory shape, a new install seeded from it has the
same reference tables, and no secret or per-install state leaves in it."""
from __future__ import annotations
import io, json, zipfile

from conftest import ROOT, upload_files, GOLDEN
from test_api_tokens import make_token
from app import admin, db, settings_export
from pipeline.run_pipeline import seed_reference_tables

EXPECTED = ["item_mapping.csv", "cpi_u.csv", "goals.json", "events.json", "accounts.csv", "checkins.json", "decisions.json", "yearly_bills.csv", "tax_payments.json", "rules.json", "household.json", "manifest.json"]


def snapshot(conn) -> dict:
    """The reference tables as a new install would hold them (confirmed_at and updated_at are per-install state)."""
    q = lambda sql: [dict(r) for r in conn.execute(sql).fetchall()]
    return {"mapping": q("SELECT item, canonical_item, category, flow, note FROM mapping_rows ORDER BY item"),
            "cpi": q("SELECT month, cpi, source FROM cpi ORDER BY month"),
            "goals": q("SELECT key, value, unit, label, note FROM goals ORDER BY key"),
            "events": q("SELECT month, label, kind, person FROM life_events ORDER BY month, label"),
            "rules": db.get_setting(conn, "rules", {}), "household": db.get_setting(conn, "household", {})}


def customise(client):
    """Edits that differ from the golden seed, so the round trip proves more than reloading the same files."""
    for kind, data in (("mapping", {"item": "Pipe-weed", "canonical_item": "Pipe-weed", "category": "Fun", "flow": "expense", "note": "Longbottom Leaf"}),
                       ("events", {"m": "2025-09", "label": "Party Tree replanted, \"quoted\" and, comma", "kind": "family", "person": "Rosie"}),
                       ("goals", {"key": "giving_share", "label": "Giving", "value": "7%", "unit": "share of income", "note": ""})):
        assert client.post(f"/settings/{kind}/row", data=data).json()["ok"], kind
    client.post("/settings/rules/people", data={"people": "Rosie, Sam", "emp_match": ["hobbiton mill"], "emp_name": ["Hobbiton Mill"]})
    client.post("/settings/household/form", data={"name": "Bagshot Row", "title": "Bagshot Ledger", "currency": "eur", "locale": "en-GB"})
    with db.pool.connection() as conn:
        assert db.get_setting(conn, "rules", {})["people"] == ["Rosie", "Sam"] and db.get_setting(conn, "household", {})["name"] == "Bagshot Row"


def test_zip_round_trips_into_a_new_install(client, reset_db, tmp_path):
    customise(client)
    r = client.get("/export/settings.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.namelist() == EXPECTED
    with db.pool.connection() as conn:
        before = snapshot(conn)
    seed = tmp_path / "seed"; zf.extractall(seed)
    with db.pool.connection() as conn:   # a new install: empty reference tables, then first-boot seeding from the unzipped files
        conn.execute("TRUNCATE item_mapping, cpi, goals, life_events, settings")
        seed_reference_tables(conn, seed); db.seed_household(conn, seed); conn.commit()
        after = snapshot(conn)
    assert after == before
    assert after["household"]["name"] == "Bagshot Row" and any(e["label"].startswith("Party Tree") and e["kind"] == "family" and e["person"] == "Rosie" for e in after["events"])


def test_manifest_names_version_household_and_migrations(client, reset_db):
    m = json.loads(zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content)).read("manifest.json"))
    assert m["app_version"] == (ROOT / "version.txt").read_text().strip()
    assert m["household"] == "Household" and m["files"] == EXPECTED[:-1]
    assert "0001_baseline.sql" in m["migrations"] and m["exported_at"].endswith("Z")


def test_no_secret_or_per_install_state_leaves(client, reset_db):
    token = make_token()
    client.post("/webhooks", data={"url": "https://hooks.example/bag-end", "secret": "mellon-speak-friend", "enabled": "on"})
    upload_files(client, GOLDEN / "exports")
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO reviews (month, model, sections, prompt) VALUES ('2025-01', 'x', '[]', 'the prompt text Gandalf sent')"); conn.commit()
    zf = zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content))
    blob = b"".join(zf.read(n) for n in zf.namelist())
    for secret in (token.encode(), b"mellon-speak-friend", b"hooks.example", b"Gandalf"):
        assert secret not in blob, secret


def test_admin_export_settings_writes_the_same_files(client, reset_db, tmp_path, capsys):
    assert admin.main(["export-settings", str(tmp_path / "out")]) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(EXPECTED)
    assert "SEED_DIR" in capsys.readouterr().out


def test_version_file_ships_in_the_image():
    assert "COPY version.txt" in (ROOT / "Dockerfile").read_text()
    assert settings_export.app_version() == (ROOT / "version.txt").read_text().strip()
