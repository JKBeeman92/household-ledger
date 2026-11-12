"""The internal format (issue #161): YYYY-MM-transactions.csv / YYYY-MM-budget.csv with ISO dates. The standalone pipeline scripts read
it and EveryDollar's own names alike, with the same output, and the app accepts both."""
from __future__ import annotations
import csv, json, shutil

import pytest

from conftest import GOLDEN, upload_files
from test_pipeline_golden import EXPECTED, REFS, run_scripts
from pipeline import filenames


def neutral_copy(src_dir, dst_dir):
    """The golden exports renamed to the internal format, dates rewritten YYYY-MM-DD."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(src_dir.glob("*.csv")):
        kind, month = filenames.parse(f.name)
        rows = list(csv.DictReader(open(f, newline="", encoding="utf-8-sig")))
        if kind == "transactions":
            rows = [{**r, "Date": "-".join(filenames.iso_date(r["Date"]))} for r in rows]
        with open(dst_dir / filenames.neutral_name(kind, month), "w", newline="") as out:
            w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n"); w.writeheader(); w.writerows(rows)


def build(wd, exports):
    (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir()
    for f in sorted(exports.glob("*.csv")):
        shutil.copy(f, wd / ("txns" if filenames.parse(f.name)[0] == "transactions" else "summaries") / f.name)
    for n in REFS:
        shutil.copy(GOLDEN / n, wd / n)
    run_scripts(wd)
    return wd


def test_filenames_recognise_both_schemes_and_nothing_else():
    assert filenames.parse("2025-03-transactions.csv") == ("transactions", "2025-03")
    assert filenames.parse("2025-03-budget.csv") == ("budget_items", "2025-03")
    assert filenames.parse("c0ffee-03-2025-EveryDollar-BudgetItems.csv") == ("budget_items", "2025-03")
    for bad in ("2025-13-budget.csv", "2025-3-budget.csv", "march-transactions.csv", "c0ffee-03-2025-EveryDollar-Other.csv"):
        assert filenames.parse(bad) is None, bad
    assert filenames.iso_date("2025-03-02") == filenames.iso_date("03/02/2025") == ("2025", "03", "02")


def test_standalone_scripts_give_identical_output_on_either_naming(tmp_path):
    neutral_copy(GOLDEN / "exports", tmp_path / "neutral")
    a = build(tmp_path / "ed", GOLDEN / "exports")
    b = build(tmp_path / "nt", tmp_path / "neutral")
    strip = lambda p: [{k: v for k, v in r.items() if k != "SourceFile"} for r in csv.DictReader(open(p))]
    assert strip(b / "ledger.csv") == strip(a / "ledger.csv") == strip(EXPECTED / "ledger.csv")
    for p in sorted((a / "dash_data").glob("*.json")):
        assert json.loads((b / "dash_data" / p.name).read_text()) == json.loads(p.read_text()), p.name


def test_the_app_takes_internal_format_uploads_and_stages_them(client, reset_db, tmp_path):
    from app import db
    neutral_copy(GOLDEN / "exports", tmp_path / "neutral")
    upload_files(client, tmp_path / "neutral")
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM source_files").fetchone()["n"] == 8
        rec = conn.execute("SELECT rows FROM datasets WHERE name='reconciliation'").fetchone()["rows"]
    assert rec == []


def test_builtin_everydollar_profile_reads_its_files_as_the_no_mapping_path_does(client, reset_db):
    from pipeline import sources as src
    from pipeline.run_pipeline import parse_export
    p = src.validate_profile(json.loads(client.get("/settings/sources/everydollar.json").text))
    assert p["name"] == "EveryDollar (built in)" and "EveryDollar (built in)" in client.get("/settings/sources").text
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        kind, _, rows = parse_export(f.name, f.read_bytes())
        via = src.apply(kind, f.read_bytes(), p)
        norm = lambda rs: [{k: ("-".join(filenames.iso_date(v)) if k == "Date" else v) for k, v in r.items() if k in via[0]} for r in rs]
        assert norm(via) == norm(rows), f.name


def test_bad_months_and_dates_are_refused_not_misread():
    assert filenames.parse("c0ffee-13-2025-EveryDollar-Transactions.csv") is None and filenames.parse("c0ffee-00-2025-EveryDollar-Transactions.csv") is None
    for bad in ("03-02-2025", "2025-03-02T10:00", "2025/03/02", "2025-02-30", "", "Feb 3 2025"):
        with pytest.raises(ValueError, match="not a date|day is out of range"):
            filenames.iso_date(bad)
    assert filenames.iso_date(" 3/2/2025 ") == ("2025", "03", "02")


def test_reconcile_and_inventory_scripts_agree_on_either_naming(tmp_path):
    import subprocess, sys
    from test_pipeline_golden import PIPELINE
    neutral_copy(GOLDEN / "exports", tmp_path / "neutral")
    outs = {}
    for name, src in (("ed", GOLDEN / "exports"), ("nt", tmp_path / "neutral")):
        wd = build(tmp_path / name, src)
        for s in ("reconcile.py", "inventory.py"):
            p = subprocess.run([sys.executable, "-I", str(PIPELINE / s)], cwd=wd, capture_output=True, text=True, timeout=60)
            assert p.returncode == 0, p.stderr
        strip = lambda f: [{k: v for k, v in r.items() if k != "SourceFile"} for r in csv.DictReader(open(wd / f))]
        outs[name] = {f: strip(f) for f in ("reconciliation_new_rows.csv", "new_rows_with_budget_month.csv", "item_inventory.csv")}
    assert outs["ed"] == outs["nt"]


def test_sources_zip_gives_internal_format_files_back_with_iso_dates(client, reset_db, tmp_path):
    import io, zipfile
    neutral_copy(GOLDEN / "exports", tmp_path / "neutral")
    upload_files(client, tmp_path / "neutral")
    z = zipfile.ZipFile(io.BytesIO(client.get("/export/sources.zip").content))
    rows = list(csv.DictReader(io.StringIO(z.read("2025-01-transactions.csv").decode())))
    assert rows and all(len(r["Date"]) == 10 and r["Date"][4] == "-" for r in rows)
