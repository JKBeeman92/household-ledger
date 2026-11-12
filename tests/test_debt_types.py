"""Debt types and the Debts tab (issue #141): a nullable debt_type on the canonical item, the CSV's optional sixth column DebtType,
the debts dataset's debt_type and paid_off, and every way a type gets in and out."""
from __future__ import annotations
import io, json, shutil, zipfile

import pytest

from conftest import GOLDEN, SAMPLE, upload_files
from test_pipeline_golden import REFS, run_scripts
from test_rules_defaults import ds
from app import admin, db
from pipeline import mapping_store

FIVE = "Item,CanonicalItem,Category,Flow,Note\n"


def mapping_csv(conn) -> str:
    from app.routes.settings import _mapping_csv
    return _mapping_csv(conn)


def test_untyped_by_default_and_five_column_imports_keep_types(client, reset_db):
    with db.pool.connection() as conn:
        assert set(mapping_store.debt_types_of(conn).values()) == {None}
        mapping_store.set_debt_type(conn, "Student Loan", "student"); conn.commit()
        body = mapping_csv(conn)
    assert body.splitlines()[0] == "Item,CanonicalItem,Category,Flow,Note,DebtType" and "Student Loan,Student Loan,Debt,debt,,student" in body
    five = FIVE + "".join(",".join(l.split(",")[:5]) + "\n" for l in body.splitlines()[1:])   # the same mapping, five columns
    r = client.post("/settings/mapping", data={"body": five})
    assert "Not saved" not in client.get(r.headers["location"]).text
    with db.pool.connection() as conn:
        assert mapping_store.debt_types_of(conn)["Student Loan"] == "student"   # a five-column CSV leaves types alone
    r = client.post("/settings/mapping", data={"body": body.replace(",student", ",")})
    with db.pool.connection() as conn:   # with the column, an empty DebtType clears it
        assert mapping_store.debt_types_of(conn)["Student Loan"] is None


def test_bad_or_conflicting_debt_types_are_refused_by_line(client, reset_db):
    body = "Item,CanonicalItem,Category,Flow,Note,DebtType\nA,Car,Debt,debt,,auto\nB,Car,Debt,debt,,card\nC,Boat,Debt,debt,,yacht\n"
    page = client.get(client.post("/settings/mapping", data={"body": body}).headers["location"]).text
    assert "line 3: Car is given DebtType card, but line 2 gives it auto" in page and "line 4: DebtType &#39;yacht&#39; is not one of" in page


def test_settings_cli_and_zip_carry_the_type(client, reset_db, capsys):
    page = client.get("/settings").text
    assert 'action="/settings/mapping/canonical/debt-type"' in page and 'aria-label="Debt type of Student Loan"' in page
    assert client.post("/settings/mapping/canonical/debt-type", data={"name": "Student Loan", "debt_type": "student"}).status_code == 303
    zf = zipfile.ZipFile(io.BytesIO(client.get("/export/settings.zip").content))
    assert "Student Loan,Student Loan,Debt,debt,,student" in zf.read("item_mapping.csv").decode()
    assert admin.main(["set-debt-type", "Student Loan", "personal"]) == 0
    assert admin.main(["set-debt-type", "Student Loan", "pirate"]) == 1
    with db.pool.connection() as conn:
        assert mapping_store.debt_types_of(conn)["Student Loan"] == "personal"


def test_seeds_and_the_sample_household_carry_debt_types(client, reset_db):
    reset_db(SAMPLE)
    with db.pool.connection() as conn:
        assert mapping_store.debt_types_of(conn)["Student Loan"] == "student"


def build(tmp_path, mapping_extra: str = "", rules: dict | None = None):
    wd = tmp_path; (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):
        shutil.copy(f, wd / ("txns" if "Transactions" in f.name else "summaries") / f.name)
    for n in REFS:
        shutil.copy(GOLDEN / n, wd / n)
    if mapping_extra:
        lines = (GOLDEN / "item_mapping.csv").read_text().splitlines()
        (wd / "item_mapping.csv").write_text(lines[0] + ",DebtType\n" + "".join(l + (",student" if l.startswith("Student Loan,") else ",") + "\n" for l in lines[1:]))
    if rules is not None:
        (wd / "rules.json").write_text(json.dumps({**json.loads((GOLDEN / "rules.json").read_text()), **rules}))
    run_scripts(wd)
    return wd


def test_debts_dataset_carries_type_and_paid_off(tmp_path):
    plain = ds(build(tmp_path / "a"), "debts")
    assert plain == [{"item": "Student Loan", "first": "2025-01", "last": "2025-04", "total": 1100.0, "months": 4, "debt_type": None, "paid_off": False}]
    typed = ds(build(tmp_path / "b", mapping_extra="yes"), "debts")
    assert typed[0]["debt_type"] == "student"
    # the golden's last month is 2025-04, so the loan is current; a window of 0 folds nothing, and the check is on the latest months
    assert ds(build(tmp_path / "c", rules={"debt_paid_off_months": 0}), "debts")[0]["paid_off"] is False


def test_paid_off_needs_no_payment_in_the_window(tmp_path):
    wd = tmp_path; (wd / "txns").mkdir(parents=True); (wd / "summaries").mkdir()
    for f in sorted((GOLDEN / "exports").glob("*.csv")):   # drop the loan's rows from the last two months
        text = f.read_text()
        if "-03-2025-" in f.name or "-04-2025-" in f.name:
            text = "\n".join(l for l in text.splitlines() if "Student Loan" not in l) + "\n"
        (wd / ("txns" if "Transactions" in f.name else "summaries") / f.name).write_text(text)
    for n in REFS:
        shutil.copy(GOLDEN / n, wd / n)
    (wd / "rules.json").write_text(json.dumps({**json.loads((GOLDEN / "rules.json").read_text()), "debt_paid_off_months": 2}))
    run_scripts(wd)
    assert ds(wd, "debts")[0]["paid_off"] is True and ds(wd, "debts")[0]["last"] == "2025-02"


def test_merging_canonical_items_keeps_a_debt_type(client, reset_db):
    from app import renames
    with db.pool.connection() as conn:
        mapping_store.write_rows(conn, [{"item": "Car", "canonical_item": "Car Loan", "category": "Debt", "flow": "debt", "note": "", "debt_type": "auto"},
                                        {"item": "Auto", "canonical_item": "Auto Loan", "category": "Debt", "flow": "debt", "note": ""}])
        renames.rename_canonical(conn, "Car Loan", "Auto Loan", "t")
        assert mapping_store.debt_types_of(conn)["Auto Loan"] == "auto"
        mapping_store.write_rows(conn, [{"item": "Card", "canonical_item": "Visa", "category": "Debt", "flow": "debt", "note": "", "debt_type": "card"}])
        msg = renames.rename_canonical(conn, "Visa", "Auto Loan", "t")
        assert mapping_store.debt_types_of(conn)["Auto Loan"] == "auto" and "debt type auto kept; Visa was card" in msg
        conn.rollback()
