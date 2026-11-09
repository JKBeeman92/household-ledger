"""End to end on examples/sample-household (what a first-time user loads): import it, expect a clean reconciliation and a full build."""
from __future__ import annotations
import os

import psycopg

from conftest import SAMPLE
from pipeline import import_exports
from pipeline.run_pipeline import reconcile


def test_sample_household_imports_and_reconciles_clean(client, reset_db):
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rec = reconcile(conn)
        assert rec["clean"], rec["problems"]
        assert conn.execute("SELECT ok FROM build_log ORDER BY id DESC LIMIT 1").fetchone() == (True,)
    assert client.get("/api/status").json()["months"] == 18
    data = client.get("/api/datasets").json()
    assert len(data["monthly"]) == 18 and data["budget_items"] and data["yearly"]
    assert client.get("/").status_code == 200
