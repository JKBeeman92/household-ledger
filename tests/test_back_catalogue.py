"""A back catalogue from another budget tool (#165): zips, the import directory and import_exports read through source profiles.

The test source, "Tally", is the sample household generated for 24 months instead of 18 and rewritten as another tool might write
it: other column names, separate money-out and money-in columns, day-first dates, spending positive in the budget file. Its 2025
files carry the month in their names (the profile's filename pattern); its 2026 files are "Tally export (n).csv", so a transactions
file's month is the most common month of its dates and a budget file is paired with the transactions file it reconciles with."""
from __future__ import annotations
import csv, importlib.util, io, json, zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import ROOT, SAMPLE, TABLES
from pipeline import filenames, sources as src

T_HEAD = ["Booked", "Payee", "Envelope group", "Envelope", "Money out", "Money in", "Account", "Memo"]
B_HEAD = ["Envelope group", "Envelope", "Assigned", "Activity"]
TALLY = {"name": "Tally",
         "transactions": {"headers": T_HEAD, "columns": {"date": "Booked", "amount": None, "debit": "Money out", "credit": "Money in", "type": None,
                                                         "merchant": "Payee", "account": "Account", "note": "Memo", "group": "Envelope group", "item": "Envelope"},
                          "date_format": "DD/MM/YYYY", "decimal": ".", "thousands": ",", "amount_mode": "split", "expense_sign": "negative",
                          "income_values": [], "income_groups": ["Income"]},
         "budget": {"headers": B_HEAD, "columns": {"group": "Envelope group", "item": "Envelope", "planned": "Assigned", "spent": "Activity"},
                    "decimal": ".", "thousands": ",", "spent_sign": "positive", "income_groups": ["Income"]},
         "month_from": {"filename": r"tally-(?P<yyyy>\d{4})-(?P<mm>\d{2})-", "column": "date", "rule": "mode"}}


@pytest.fixture(scope="module")
def household24(tmp_path_factory) -> Path:
    """The sample household's generator, run for 24 months into a folder of its own (the shipped 18 months are not touched)."""
    out = tmp_path_factory.mktemp("sample24")
    spec = importlib.util.spec_from_file_location("sample_generate", SAMPLE / "generate.py")
    gen = importlib.util.module_from_spec(spec); spec.loader.exec_module(gen)
    gen.MONTHS = 24
    gen.main(out)
    return out


def _rows(path: Path) -> list[dict]:
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def to_tally(folder: Path, month: str) -> tuple[bytes, bytes]:
    """One month of the generated household in Tally's layout: (transactions, budget)."""
    names = {filenames.parse(p.name)[0]: p for p in folder.glob("*.csv") if filenames.budget_month(p.name) == month}
    t, b = io.StringIO(), io.StringIO()
    w = csv.writer(t, lineterminator="\n"); w.writerow(T_HEAD)
    for r in _rows(names["transactions"]):
        a = Decimal(r["Amount"]); yy, mm, dd = filenames.iso_date(r["Date"])
        w.writerow([f"{dd}/{mm}/{yy}", r["Merchant"], r["Group"], r["Item"], f"{-a:.2f}" if a < 0 else "", f"{a:.2f}" if a > 0 else "", r["Account"], r["Note"]])
    w = csv.writer(b, lineterminator="\n"); w.writerow(B_HEAD)
    for r in _rows(names["budget_items"]):
        s = Decimal(r["Spent"])
        w.writerow([r["Group"], r["Item"], r["Planned"], f"{s if r['Group'] == 'Income' else -s:.2f}"])
    return t.getvalue().encode(), b.getvalue().encode()


def tally_files(folder: Path, months: list[str]) -> dict[str, bytes]:
    """{file name: bytes} for these months: dated names for 2025, numbered undated ones ("Tally export (n).csv") for later years."""
    out, n = {}, 0
    for m in months:
        t, b = to_tally(folder, m)
        if m < "2026":
            out[f"tally-{m}-register.csv"], out[f"tally-{m}-envelopes.csv"] = t, b
        else:
            out[f"Tally export ({n + 1}).csv"], out[f"Tally export ({n + 2}).csv"] = (t, b) if n % 4 == 0 else (b, t)   # the order tells nothing
            n += 2
    return out


def months_of(folder: Path) -> list[str]:
    return sorted({filenames.budget_month(p.name) for p in folder.glob("*.csv") if filenames.is_export(p.name)})


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in entries.items():
            z.writestr(f"tally/{n}", d)
    return buf.getvalue()


def save_tally(spec: dict = TALLY) -> int:
    from app import db
    from pipeline.run_pipeline import save_profile
    with db.pool.connection() as conn:
        return save_profile(conn, json.loads(json.dumps(spec)))


def count(sql: str):
    from app import db
    with db.pool.connection() as conn:
        return conn.execute(sql).fetchone()["n"]


# ---------------------------------------------------------------- the month rule, no database
def tx(*dates: str) -> bytes:
    return ("\n".join([",".join(T_HEAD)] + [f"{d},Shop,Food,Groceries,1.00,,Acct," for d in dates]) + "\n").encode()


def test_month_from_dates_takes_the_most_common_month_and_refuses_a_tie_or_nothing():
    sec = TALLY["transactions"]
    assert src.month_of_dates(tx("31/12/2025", "02/01/2026", "15/01/2026"), sec) == ("2026-01", "the most common month of its dates (2 of 3)")
    month, how = src.month_of_dates(tx("31/12/2025", "02/01/2026"), sec)
    assert month is None and "2025-12 as in 2026-01" in how
    assert src.month_of_dates(tx("", "not a date"), sec) == (None, "no transaction date could be read")
    assert src.month_of_dates(("\n".join([",".join(T_HEAD)]) + "\n").encode(), sec) == (None, "no transaction date could be read")


def test_guess_month_name_first_then_dates_and_never_without_a_rule_unless_shown():
    p = json.loads(json.dumps(TALLY))
    assert src.guess_month("x/tally-2025-03-register.csv", tx("01/04/2025"), p, "transactions") == ("2025-03", "from the file name")
    assert src.guess_month("export.csv", tx("01/04/2025"), p, "transactions")[0] == "2025-04"
    assert src.guess_month("tally-2025-13-register.csv", tx("01/04/2025"), p, "transactions")[0] == "2025-04"   # no month 13: the dates decide
    assert src.guess_month("export.csv", b"", p, "budget_items") == (None, "a budget file has no dates")
    del p["month_from"]
    assert src.guess_month("export.csv", tx("01/04/2025"), p, "transactions")[0] is None   # the import directory: no rule, no guess
    month, how = src.guess_month("export.csv", tx("01/04/2025"), p, "transactions", default_mode=True)
    assert month == "2025-04" and "a guess" in how   # a zip or the command line, which show it before storing
    p["month_from"] = {"filename": r"(?P<yyyy>\d{4})(?P<mm>\d{2})"}
    assert src.guess_month("202511.csv", tx("01/04/2025"), p, "transactions") == ("2025-11", "from the file name")
    assert src.guess_month("export.csv", tx("01/04/2025"), p, "transactions", default_mode=True)[0] is None   # a name rule only: no fallback


@pytest.mark.parametrize("mf, says", [({"filename": r"(\d{4})-(\d{2})"}, "(?P<yyyy>"), ({"filename": "("}, "not a regular expression"),
                                       ({"column": "posted", "rule": "mode"}, "the only date rule"), ({"rule": "median"}, "the only date rule"),
                                       ({"month": "x"}, "month_from must be"), ("mode", "month_from must be")])
def test_bad_month_rules_are_refused_with_the_reason(mf, says):
    with pytest.raises(src.ProfileError, match=None) as e:
        src.validate_profile(dict(TALLY, month_from=mf))
    assert says in str(e.value)


def test_plan_pairs_by_month_and_kind_and_names_what_it_will_not_store(household24):
    files = tally_files(household24, ["2025-01", "2025-02", "2026-01", "2026-02"])
    t3, _ = to_tally(household24, "2025-03")
    files |= {"tally-2025-03-register.csv": t3,                        # unpaired: no budget file for March
              "tally-2025-01-register (copy).csv": files["tally-2025-01-register.csv"],   # a second January
              "notes.csv": b"When,What\n1,2\n"}                           # no profile has these columns
    rows = [{"id": 1, "name": "Tally", "spec": TALLY}]
    plan = {e["name"]: e for e in src.plan_batch(list(files.items()), rows, present={("budget_items", "2025-02")})}
    assert plan["notes.csv"]["state"] == "refused" and "no saved source profile" in plan["notes.csv"]["detail"]
    assert "unpaired" in plan["tally-2025-03-register.csv"]["detail"]
    assert all("more than one transactions file for 2025-01" in plan[n]["detail"] for n in ("tally-2025-01-register.csv", "tally-2025-01-register (copy).csv"))
    assert "named like an export" in plan["tally-2025-02-envelopes.csv"]["detail"]   # the batch's own EveryDollar file wins
    assert plan["tally-2025-02-register.csv"]["state"] == "ready"   # its other half is that named file
    undated = [e for e in plan.values() if e["name"].startswith("Tally export")]
    assert sorted((e["month"], e["kind"], e["state"]) for e in undated) == [("2026-01", "budget_items", "ready"), ("2026-01", "transactions", "ready"),
                                                                             ("2026-02", "budget_items", "ready"), ("2026-02", "transactions", "ready")]
    assert all(e["how"].startswith("paired with Tally export") for e in undated if e["kind"] == "budget_items")
    assert all(e["how"].startswith("the most common month of its dates") for e in undated if e["kind"] == "transactions")
    stored = src.plan_batch(list(files.items()), rows, stored={("transactions", "2025-03"): "x", ("budget_items", "2025-03"): "y"})
    assert next(e for e in stored if e["name"] == "tally-2025-03-register.csv")["detail"] == "replaces the stored 2025-03 file"


# ---------------------------------------------------------------- through the app
def test_a_zip_of_24_months_stores_in_one_confirmation_and_one_rebuild_and_reconciles(client, reset_db, household24):
    from app import db
    from pipeline.run_pipeline import reconcile
    from test_long_lists import problems
    reset_db(household24)
    save_tally()
    months = months_of(household24)
    assert len(months) == 24
    r = client.post("/upload", files=[("files", ("tally-back-catalogue.zip", make_zip(tally_files(household24, months)), "application/zip"))])
    assert r.status_code == 303 and r.headers["location"].startswith("/upload/batch/"), r.headers.get("location")
    assert count("SELECT count(*) n FROM source_files") == 0 and count("SELECT count(*) n FROM build_log") == 0   # the dry run stores nothing
    batch = r.headers["location"]
    page = client.get(batch).text
    assert "48 files ready to store, 24 budget months" in page and "January 2025 to December 2026" in page
    assert "from the file name" in page and "the most common month of its dates" in page and "paired with Tally export" in page
    # the UX review of #165: months in the table read "Jan 2025" (DS-WORD-6), the filter says what it left, and the table scrolls by keyboard
    assert '<td class="nowrap mono">Jan 2025</td>' in page and 'id="batch-filter-count" aria-live="polite"' in page
    assert 'aria-label="What would be stored, file by file"' in page
    assert 'data-list="unbounded"' in page and problems(page) == [] and 'id="batch-filter"' in page
    r = client.post(batch)
    assert r.status_code == 303 and r.headers["location"] == "/upload"
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM build_log").fetchone()["n"] == 1   # one rebuild for the whole batch
        assert conn.execute("SELECT ok FROM build_log").fetchone()["ok"] is True
        assert conn.execute("SELECT count(*) n FROM source_files WHERE profile_id IS NOT NULL").fetchone()["n"] == 48
        assert conn.execute("SELECT count(*) n FROM upload_pending").fetchone()["n"] == 0
        assert reconcile(conn)["clean"]
        tally_ledger = conn.execute("SELECT budget_month, date, item, canonical_item, merchant, amount FROM ledger ORDER BY 1, 2, 3, 5, 6").fetchall()
    assert client.get("/api/status").json()["months"] == 24
    up = client.get("/upload").text
    assert "Stored 48 files (24 months)" in up and "48 of 48 files stored" in up
    # the same household loaded from its own exports builds the same ledger
    reset_db(household24)
    from pipeline import import_exports
    with db.pool.connection() as conn:
        assert import_exports.run(conn, [str(household24)], out=lambda s: None) == 0
        assert conn.execute("SELECT budget_month, date, item, canonical_item, merchant, amount FROM ledger ORDER BY 1, 2, 3, 5, 6").fetchall() == tally_ledger


def test_a_zip_of_unknown_files_is_refused_until_one_month_is_mapped(client, reset_db, household24):
    reset_db(household24)
    r = client.post("/upload", files=[("files", ("x.zip", make_zip(tally_files(household24, ["2025-01", "2025-02"])), "application/zip"))])
    assert r.headers["location"] == "/upload"
    page = client.get("/upload").text
    assert "0 of 4 files stored" in page and "no saved source profile recognises its columns" in page and "on its own first" in page


def test_the_batch_can_be_discarded_and_a_mapping_edit_keeps_the_month_rule(client, reset_db, household24):
    from test_sources import pending_of, form_for
    reset_db(household24)
    save_tally()
    r = client.post("/upload", files=[("files", (n, d, "text/csv")) for n, d in tally_files(household24, ["2025-01", "2025-02"]).items()])
    assert r.headers["location"].startswith("/upload/batch/")   # more than two files: the batch, not the one-month mapping page
    assert client.post(r.headers["location"] + "/discard").headers["location"] == "/upload"
    assert count("SELECT count(*) n FROM upload_pending") == 0 and count("SELECT count(*) n FROM source_files") == 0
    t, b = to_tally(household24, "2025-03")
    r = client.post("/upload", files=[("files", ("march-transactions.csv", t, "text/csv")), ("files", ("march-budget.csv", b, "text/csv"))])
    batch = r.headers["location"].rsplit("/", 1)[1]
    assert r.headers["location"].startswith("/upload/map/")   # one month's pair still goes to the mapping page
    r = client.post(f"/upload/map/{batch}", data=form_for(TALLY, pending_of(batch), month="2025-03", name="Tally"))
    assert r.status_code == 303
    from app import db
    with db.pool.connection() as conn:
        assert conn.execute("SELECT spec FROM source_profiles").fetchone()["spec"]["month_from"] == TALLY["month_from"]


def _snapshot() -> dict:
    from app import db
    with db.pool.connection() as conn:
        return {t: conn.execute(f"SELECT count(*) n, md5(coalesce(string_agg(x::text, ',' ORDER BY x::text), '')) h FROM {t} x").fetchone()
                for t in (s.strip() for s in TABLES.split(","))}


def test_a_dry_run_stores_nothing_then_the_command_line_import_loads_it(client, reset_db, household24, tmp_path, capsys):
    from app import admin
    from pipeline import import_exports
    reset_db(household24)
    save_tally()
    for n, d in tally_files(household24, ["2025-01", "2025-02", "2026-01"]).items():
        (tmp_path / n).write_bytes(d)
    (tmp_path / "notes.csv").write_bytes(b"When,What\n1,2\n")
    before = _snapshot()
    assert import_exports.main(["--profile", "Tally", "--dry-run", str(tmp_path)]) == 1   # notes.csv would be refused
    assert admin.main(["import", str(tmp_path), "--profile", "Tally", "--dry-run"]) == 1
    assert _snapshot() == before   # no table touched, not even the build log
    out = capsys.readouterr().out
    assert out.count("would store") == 12 and out.count("REFUSED") == 2 and "nothing stored" in out and "no saved source profile" in out
    assert "2026-01 budget_items" in out and "paired with Tally export" in out
    assert import_exports.main(["--profile", "Nobody", "--dry-run", str(tmp_path)]) == 2
    (tmp_path / "notes.csv").unlink()
    assert admin.main(["import", str(tmp_path), "--profile", "Tally"]) == 0
    assert client.get("/api/status").json()["months"] == 3
    assert count("SELECT count(*) n FROM build_log") == 1
    assert import_exports.main([str(tmp_path)]) == 2   # without --profile only files named like exports are read, as before


def test_import_dir_loads_everydollar_and_profiled_files_and_names_an_unmatched_one(client, reset_db, household24, tmp_path):
    from app import db
    from pipeline.run_pipeline import import_dir
    reset_db(household24)
    save_tally()
    for m in ("2025-01", "2025-02"):
        for p in household24.glob(f"*-{m[5:]}-{m[:4]}-EveryDollar-*.csv"):
            (tmp_path / p.name).write_bytes(p.read_bytes())
    sub = tmp_path / "tally"; sub.mkdir()
    for n, d in tally_files(household24, ["2025-03", "2026-01"]).items():
        (sub / n).write_bytes(d)
    (sub / "stranger.csv").write_bytes(b"When,What,How much\n2025-03-01,Tea,2\n")
    (tmp_path / ".other").mkdir(); (tmp_path / ".other" / "statement.csv").write_bytes(b"When,What\n1,2\n")   # under a dot-folder: left alone
    (sub / ".notes.csv").write_bytes(b"When,What\n1,2\n")
    (tmp_path / "item_mapping.csv").write_bytes((household24 / "item_mapping.csv").read_bytes())   # a seed file: not an export, not named
    with db.pool.connection() as conn:
        res = import_dir(conn, tmp_path)
        assert res["loaded"] == 8 and [f["file"] for f in res["failed"]] == ["tally/stranger.csv"]
        assert client.get("/api/status").json()["months"] == 4
        assert conn.execute("SELECT count(*) n FROM source_files WHERE filename='stranger.csv'").fetchone()["n"] == 0
        last = conn.execute("SELECT ok, summary, detail FROM build_log ORDER BY id DESC LIMIT 1").fetchone()
        assert last["ok"] is False and "stranger.csv" in last["detail"] and "no saved source profile recognises its columns" in last["detail"]
        res = import_dir(conn, tmp_path)   # the next start: everything already loaded is skipped, the stranger is named again
        assert res["loaded"] == 0 and res["skipped"] == 8 and len(res["failed"]) == 1
        # without a month rule the unattended import does not guess: the undated files are named, the dated ones too
        spec = json.loads(json.dumps(TALLY)); del spec["month_from"]
        from pipeline.run_pipeline import save_profile
        save_profile(conn, spec)
        conn.execute("DELETE FROM source_files WHERE profile_id IS NOT NULL"); conn.commit()
        res = import_dir(conn, tmp_path)
        assert res["loaded"] == 0 and all("no budget month" in f["error"] or "no saved source profile" in f["error"] for f in res["failed"]) and len(res["failed"]) == 5


def test_import_dir_without_profiles_ignores_other_csvs_as_before(client, reset_db, tmp_path):
    from app import db
    from pipeline.run_pipeline import import_dir
    from conftest import GOLDEN
    for p in (GOLDEN / "exports").glob("*.csv"):
        (tmp_path / p.name).write_bytes(p.read_bytes())
    (tmp_path / "notes.csv").write_bytes(b"a,b\n1,2\n")
    with db.pool.connection() as conn:
        res = import_dir(conn, tmp_path)
    assert res["loaded"] == 8 and res["failed"] == []


# ---------------------------------------------------------------- review follow-ups
def test_a_mixed_zip_waits_whole_and_its_own_export_wins_the_month(client, reset_db, household24):
    """A zip with EveryDollar's files and another tool's: nothing is stored before the one confirmation, the named export wins its
    month over the profiled file for it (as on the command line), and the whole zip is one rebuild."""
    from app import db
    reset_db(household24)
    save_tally()
    entries = {p.name: p.read_bytes() for p in household24.glob("*-01-2025-EveryDollar-*.csv")}
    assert len(entries) == 2
    entries.update(tally_files(household24, ["2025-01", "2025-02"]))
    r = client.post("/upload", files=[("files", ("mixed.zip", make_zip(entries), "application/zip"))])
    assert r.headers["location"].startswith("/upload/batch/")
    assert count("SELECT count(*) n FROM source_files") == 0 and count("SELECT count(*) n FROM build_log") == 0
    page = client.get(r.headers["location"]).text
    assert "4 files ready to store, 2 budget months" in page and "2 files are named like exports" in page
    assert page.count("the batch also has a") == 2 and "replaces the stored" not in page
    client.post(r.headers["location"])
    with db.pool.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM build_log").fetchone()["n"] == 1
        got = conn.execute("SELECT budget_month, profile_id IS NOT NULL p FROM source_files ORDER BY 1, 2").fetchall()
    assert [(g["budget_month"], g["p"]) for g in got] == [("2025-01", False), ("2025-01", False), ("2025-02", True), ("2025-02", True)]


def test_a_real_command_line_import_shows_each_guessed_month_before_storing(client, reset_db, household24, tmp_path):
    from app import db
    from pipeline import import_exports
    reset_db(household24)
    save_tally()
    for n, d in tally_files(household24, ["2026-01"]).items():
        (tmp_path / n).write_bytes(d)
    lines = []
    with db.pool.connection() as conn:
        assert import_exports.run(conn, [str(tmp_path)], profile="Tally", out=lines.append) == 0
    first_stored = next(i for i, x in enumerate(lines) if x.startswith("stored "))
    shown = [x for x in lines[:first_stored] if x.startswith("store ")]
    assert len(shown) == 2 and any("the most common month of its dates" in x for x in shown) and all("2026-01" in x for x in shown)


def test_two_profiled_files_of_one_name_both_reach_sources_zip(client, reset_db, household24, tmp_path):
    """transactions/2025-01.csv and budget/2025-01.csv are stored as two files named 2025-01.csv; the archive keeps both."""
    from pipeline import import_exports
    from app import db
    reset_db(household24)
    spec = json.loads(json.dumps(TALLY)); spec["month_from"] = {"filename": r"(?P<yyyy>\d{4})-(?P<mm>\d{2})"}
    save_tally(spec)
    t, b = to_tally(household24, "2025-01")
    for d, data in (("transactions", t), ("budget", b)):
        (tmp_path / d).mkdir(); (tmp_path / d / "2025-01.csv").write_bytes(data)
    with db.pool.connection() as conn:
        assert import_exports.run(conn, [str(tmp_path)], profile="Tally", out=lambda s: None) == 0
    z = zipfile.ZipFile(io.BytesIO(client.get("/export/sources.zip").content))
    names = sorted(n for n in z.namelist() if n.startswith("2025-01/"))
    assert names == ["2025-01/2025-01.csv", "2025-01/transactions-2025-01.csv"]
    assert z.read("2025-01/2025-01.csv") == b and z.read("2025-01/transactions-2025-01.csv") == t


def test_the_month_in_a_batch_sentence_is_written_the_households_way():
    from app.routes.sources import _MONTH_RX
    say = lambda t: _MONTH_RX.sub(lambda m: "<" + m.group(0) + ">", t)
    assert say("replaces the stored 2026-05 file") == "replaces the stored <2026-05> file"
    # a file name, a full date and a non-month are left alone
    assert say("from the file name tally-2025-03-register.csv") == "from the file name tally-2025-03-register.csv"
    assert say("dated 2026-05-14") == "dated 2026-05-14" and say("2026-13") == "2026-13"


def test_sources_and_upload_pages_label_their_fields_and_tables(client, reset_db, household24):
    from pipeline import import_exports
    reset_db(household24)
    save_tally()
    page = client.get("/settings/sources").text
    assert '<label for="src-1-body">' in page and 'id="src-1-body"' in page and ">Save the profile</button>" in page   # no files: nothing to re-read
    assert import_exports.main([str(household24)]) == 0
    page = client.get("/upload").text   # the loaded-months table scrolls by keyboard (DS-A11Y-1)
    assert '<div class="tablewrap" role="region" tabindex="0" aria-label="Loaded months, file by file">' in page
