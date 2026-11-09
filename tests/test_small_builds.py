"""A new household's first uploads are one or two budget months: the standalone pipeline scripts must build them."""
from __future__ import annotations
import json, shutil, subprocess, sys

import pytest

from conftest import GOLDEN, ROOT, upload_files

SCRIPTS = ["build_ledger.py", "aggregate.py", "make_datasets.py", "make_budget_items.py"]
REFS = ["item_mapping.csv", "cpi_u.csv", "rules.json", "events.json"]


def build(tmp_path, months: list[str]):
    (tmp_path / "txns").mkdir(); (tmp_path / "summaries").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        if f.name.split("-")[1] in months:
            shutil.copy(f, tmp_path / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    for n in REFS:
        shutil.copy(GOLDEN / n, tmp_path / n)
    for s in SCRIPTS:
        p = subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / s)], cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, f"{s} failed:\n{p.stdout}{p.stderr}"
    return tmp_path / "dash_data"


@pytest.mark.parametrize("months", [["01"], ["01", "02"]])
def test_standalone_scripts_build_one_and_two_months(tmp_path, months):
    out = build(tmp_path, months)
    monthly = json.loads((out / "monthly.json").read_text())
    assert [r["m"] for r in monthly] == [f"2025-{m}" for m in months]
    recurring = json.loads((out / "recurring.json").read_text())
    assert recurring and all(r["active"] == (r["last"] >= f"2025-{months[0]}") for r in recurring)


def test_recurring_active_flag_unchanged_with_four_months(tmp_path):
    out = build(tmp_path, ["01", "02", "03", "04"])
    for r in json.loads((out / "recurring.json").read_text()):
        assert r["active"] == (r["last"] >= "2025-02")


def test_one_month_upload_builds_through_the_web_app(client, reset_db, tmp_path):
    d = tmp_path / "one"; d.mkdir()
    for f in (GOLDEN / "exports").glob("c0ffee-01-*.csv"):
        shutil.copy(f, d / f.name)
    upload_files(client, d)
    assert client.get("/api/status").json()["months"] == 1
    st = client.get("/api/build").json()
    assert st["state"] == "ok" and st["running"] is False, st
