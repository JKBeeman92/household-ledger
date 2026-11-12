"""Unmapped items and reconciliation problems are persisted by the build and shown on Close a month; the inline form maps and rebuilds."""
from __future__ import annotations
import json, shutil, subprocess, sys

from conftest import GOLDEN, RECON_FAIL, ROOT, upload_files


def _unmap(item):
    from app import db
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM item_mapping WHERE item=%s", (item,)); conn.commit()


def test_dataset_lists_only_problem_envelopes_standalone(tmp_path):
    (tmp_path / "txns").mkdir(); (tmp_path / "summaries").mkdir()
    for f in (RECON_FAIL / "exports").glob("*.csv"):
        shutil.copy(f, tmp_path / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    for n in ("item_mapping.csv", "cpi_u.csv", "rules.json", "events.json"):
        shutil.copy(GOLDEN / n, tmp_path / n)
    for s in ("build_ledger.py", "aggregate.py", "make_datasets.py"):
        p = subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / s)], cwd=tmp_path, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
    rows = json.loads((tmp_path / "dash_data" / "reconciliation.json").read_text())
    assert {(r["m"], r["item"], r["status"]) for r in rows} == {("2025-02", "Groceries", "MISMATCH"), ("2025-02", "Fuel", "ITEM_NOT_IN_SUMMARY")}
    g = next(r for r in rows if r["item"] == "Groceries")
    assert g["delta"] == round(g["txn_sum"] - g["spent"], 2) and g["delta"] != 0


def test_reconciliation_problems_persist_across_page_loads(client, reset_db):
    reset_db(RECON_FAIL if (RECON_FAIL / "item_mapping.csv").exists() else GOLDEN)
    upload_files(client, RECON_FAIL / "exports")
    first = client.get("/upload").text
    assert 'id="recon-bad"' in first and "Groceries" in first and "MISMATCH" in first
    again = client.get("/upload").text   # no flash is left to be popped: the page reads the stored build
    assert 'id="recon-bad"' in again and "Fuel" in again
    assert {r["item"] for r in client.get("/api/datasets").json()["reconciliation"]} == {"Groceries", "Fuel"}


def test_clean_build_says_so(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    page = client.get("/upload").text
    assert 'id="recon-clean"' in page and 'id="recon-bad"' not in page and 'id="unmapped-none"' in page
    assert client.get("/api/datasets").json()["reconciliation"] == []


def test_unmapped_item_is_listed_then_mapped_inline_and_rebuilt(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    _unmap("Electric")
    client.post("/rebuild")
    st = client.get("/api/build").json()
    assert st["state"] == "failed"
    page = client.get("/upload").text
    assert 'id="unmapped-form"' in page and 'value="Electric"' in page and 'action="/upload/map"' in page
    assert client.get("/upload").text.count('value="Electric"') >= 1   # persistent, not a flash

    r = client.post("/upload/map", data={"item": ["Electric"], "canonical": ["Electric"], "category": ["Utilities"], "flow": ["expense"]})
    assert r.status_code == 303
    assert client.get("/api/build").json()["state"] == "ok"
    page = client.get("/upload").text
    assert 'id="unmapped-none"' in page and 'value="Electric"' not in page
    from app import db
    with db.pool.connection() as conn:
        assert conn.execute("SELECT category, flow FROM mapping_rows WHERE item='Electric'").fetchone() == {"category": "Utilities", "flow": "expense"}
        assert conn.execute("SELECT count(*) n FROM ledger WHERE item='Electric'").fetchone()["n"] > 0


def test_mapping_form_rejects_a_blank_or_placeholder_category(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    _unmap("Electric")
    client.post("/rebuild")
    for cat in ("", "UNMAPPED"):
        r = client.post("/upload/map", data={"item": ["Electric"], "canonical": [""], "category": [cat], "flow": ["expense"]})
        text = client.get("/upload").text
        assert r.status_code == 303 and ("Nothing to add" in text or "Not saved" in text)
    from app import db
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM item_mapping WHERE item='Electric'").fetchone()["n"] == 0
    r = client.post("/upload/map", data={"item": ["Electric"], "canonical": [""], "category": ["Utilities"], "flow": ["bogus"]})
    assert "Not saved" in client.get("/upload").text
    client.post("/upload/map", data={"item": ["Electric"], "canonical": [""], "category": ["Utilities"], "flow": ["expense"]})  # leave it clean


def test_mapping_route_needs_an_editor(client, reset_db, monkeypatch):
    import dataclasses
    from app import auth, db
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))
    r = client.post("/upload/map", data={"item": ["Hobbit Hole Fund"], "category": ["Housing"], "flow": ["expense"]})
    assert r.status_code in (303, 401, 403) and "/upload" != r.headers.get("location")
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM item_mapping WHERE item='Hobbit Hole Fund'").fetchone()["n"] == 0
