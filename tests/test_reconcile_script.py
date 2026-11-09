"""pipeline/reconcile.py is a standalone script (`python -I reconcile.py` in a folder with txns/ and summaries/), no database."""
from __future__ import annotations
import csv, shutil, subprocess, sys

from conftest import GOLDEN, RECON_FAIL, ROOT


def run(exports, tmp_path):
    for sub in ("txns", "summaries"): (tmp_path / sub).mkdir()
    for f in sorted((exports / "exports").glob("*.csv")):
        shutil.copy(f, tmp_path / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    return subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / "reconcile.py")], cwd=tmp_path, capture_output=True, text=True, timeout=60)


def test_clean_exports_exit_zero_and_write_rows(tmp_path):
    p = run(GOLDEN, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "CLEAN" in p.stdout
    rows = list(csv.DictReader(open(tmp_path / "reconciliation_new_rows.csv")))
    assert rows and {r["Status"] for r in rows} <= {"OK", "OK_NO_ACTIVITY"}
    assert (tmp_path / "new_rows_with_budget_month.csv").exists()


def test_mismatch_exits_one_and_names_the_item(tmp_path):
    p = run(RECON_FAIL, tmp_path)
    assert p.returncode == 1
    assert "MISMATCH" in p.stdout and "Groceries" in p.stdout and "ITEM_NOT_IN_SUMMARY" in p.stdout


def test_wrong_folder_is_a_clear_error(tmp_path):
    p = subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / "reconcile.py")], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode != 0 and "txns/ and summaries/" in p.stderr
