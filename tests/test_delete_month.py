"""Deleting a loaded month removes both files, rebuilds in the background and keeps a sha256 history; replacing leaves a marker."""
from __future__ import annotations

import pytest

from conftest import GOLDEN, upload_files
from app import db


@pytest.fixture
def loaded(client, reset_db):
    upload_files(client, GOLDEN / "exports")
    return client


def q(sql, *a):
    with db.pool.connection() as conn:
        return conn.execute(sql, a).fetchall()


def months():
    return [r["budget_month"] for r in q("SELECT DISTINCT budget_month FROM source_files ORDER BY 1")]


def test_delete_removes_both_files_rows_and_rebuilds(loaded):
    ms = months(); m = ms[-1]
    assert q("SELECT count(*) n FROM source_files WHERE budget_month=%s", m)[0]["n"] == 2
    before = q("SELECT count(*) n FROM build_log")[0]["n"]
    r = loaded.post(f"/months/{m}/delete")
    assert r.status_code == 303 and "Deleted both files" in loaded.get(r.headers["location"]).text
    assert months() == ms[:-1]
    assert q("SELECT count(*) n FROM transactions WHERE budget_month=%s", m)[0]["n"] == 0
    assert q("SELECT count(*) n FROM budget_items WHERE budget_month=%s", m)[0]["n"] == 0
    assert q("SELECT count(*) n FROM build_log")[0]["n"] == before + 1
    assert loaded.get("/api/build").json()["state"] == "ok"
    assert q("SELECT count(*) n FROM ledger WHERE budget_month=%s", m)[0]["n"] == 0
    hist = q("SELECT kind, sha256, event FROM source_file_history WHERE budget_month=%s", m)
    assert sorted(h["kind"] for h in hist) == ["budget_items", "transactions"] and {h["event"] for h in hist} == {"deleted"}
    assert all(len(h["sha256"]) == 64 for h in hist)


def test_delete_unknown_or_malformed_month_changes_nothing(loaded):
    n = len(q("SELECT 1 FROM source_files"))
    assert "No files are loaded" in loaded.get(loaded.post("/months/1999-01/delete").headers["location"]).text
    assert "not a budget month" in loaded.get(loaded.post("/months/abc/delete").headers["location"]).text
    assert len(q("SELECT 1 FROM source_files")) == n and q("SELECT count(*) n FROM source_file_history")[0]["n"] == 0


def test_delete_each_month_down_to_empty_still_rebuilds(loaded):
    for m in months():
        loaded.post(f"/months/{m}/delete")
    assert months() == [] and loaded.get("/api/build").json()["state"] == "ok"
    assert "Loaded months" in loaded.get("/upload").text


def test_replacing_a_file_records_history_and_shows_a_marker(loaded, tmp_path):
    f = sorted((GOLDEN / "exports").glob("*Transactions.csv"))[0]
    old_sha = q("SELECT sha256 FROM source_files WHERE kind='transactions' ORDER BY budget_month LIMIT 1")[0]["sha256"]
    assert "replaced on" not in loaded.get("/upload").text
    data = f.read_bytes()
    loaded.post("/upload", files=[("files", (f.name, data, "text/csv"))])
    assert q("SELECT count(*) n FROM source_file_history WHERE event='replaced'")[0]["n"] == 1
    page = loaded.get("/upload").text
    assert "replaced on" in page and "identical to the previous file" in page
    # a changed file is flagged as different, with both hashes
    loaded.post("/upload", files=[("files", (f.name, data + b"\n", "text/csv"))])
    assert [h["sha256"] for h in q("SELECT sha256 FROM source_file_history WHERE event='replaced' ORDER BY id")] == [old_sha, old_sha]
    page = loaded.get("/upload").text
    assert "different from the previous file" in page and old_sha[:8] in page


def test_marker_is_not_shown_for_a_month_deleted_and_uploaded_again(loaded):
    f = sorted((GOLDEN / "exports").glob("*Transactions.csv"))[0]
    m = q("SELECT budget_month FROM source_files WHERE filename=%s", f.name)[0]["budget_month"]
    loaded.post("/upload", files=[("files", (f.name, f.read_bytes(), "text/csv"))])
    loaded.post(f"/months/{m}/delete")
    loaded.post("/upload", files=[("files", (f.name, f.read_bytes(), "text/csv"))])
    assert "replaced on" not in loaded.get("/upload").text


def test_page_offers_one_delete_per_month(loaded):
    page = loaded.get("/upload").text
    assert page.count("/delete") == len(months())
