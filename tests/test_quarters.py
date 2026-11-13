"""The quarter (#147): quarters equal the sum of their months; partial quarters are labelled and compared per month; the same quarter a
year before is hidden, not zero, when it is not loaded; year_start_month shifts the quarters; the look ahead keeps the seasons."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

from conftest import SAMPLE

_spec = importlib.util.spec_from_file_location("quarters", Path(__file__).resolve().parent.parent / "pipeline" / "quarters.py")
q = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(q)


def mrow(m, income, spend, debt=100.0):
    return {"m": m, "income": income, "spend": spend, "debt": debt, "net": income - spend - debt,
            "income_real": income, "spend_real": spend, "debt_real": debt, "net_real": income - spend - debt}


def months(first, last):
    out, (y, m) = [], map(int, first.split("-"))
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}"); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def test_quarter_labels_and_year_start():
    assert q.quarter_of("2026-08") == ("2026-Q3", ["2026-07", "2026-08", "2026-09"])
    assert q.quarter_of("2026-08", 7) == ("2026-Q1", ["2026-07", "2026-08", "2026-09"])   # a July year: July to September is Q1
    assert q.quarter_of("2027-02", 7) == ("2026-Q3", ["2027-01", "2027-02", "2027-03"])
    assert q.shift("2026-Q1", -1) == "2025-Q4" and q.shift("2026-Q3", -4) == "2025-Q3" and q.shift("2026-Q4", 1) == "2027-Q1"


def test_quarters_are_the_sum_of_their_months_and_partial_ones_say_so():
    rows = [mrow(m, 5000 + i, 3000 + i) for i, m in enumerate(months("2025-07", "2026-08"))]
    Q = {r["quarter"]: r for r in q.quarterly(rows)}
    assert Q["2025-Q3"]["income"] == 5000 + 5001 + 5002 and Q["2025-Q3"]["months"] == 3 and not Q["2025-Q3"]["partial"]
    assert Q["2026-Q3"]["months"] == 2 and Q["2026-Q3"]["partial"] and Q["2026-Q3"]["loaded"] == ["2026-07", "2026-08"]
    assert sum(r["income"] for r in Q.values()) == sum(r["income"] for r in rows)   # nothing lost or doubled


def test_comparisons_are_per_month_and_a_missing_year_is_hidden():
    rows = [mrow(m, 6000, 3000) for m in months("2025-07", "2025-12")] + [mrow(m, 6000, 3600) for m in months("2026-01", "2026-02")]
    c = q.compare("2026-Q1", q.quarterly(rows), [], monthly=rows)
    assert c["partial"] and c["months"] == 2
    assert c["this"]["spend"] == 3600 and c["previous"]["spend"] == 3000 and c["previous"]["spend_change"] == 600   # per month, not 7,200 vs 9,000
    assert c["previous"]["spend_pct"] == 0.2
    assert c["year_ago"] is None and c["movers_year_ago"] is None   # 2025-Q1 is not loaded: hidden, never zero


def test_year_start_seven_shifts_the_comparison():
    rows = [mrow(m, 5000, 2000) for m in months("2025-07", "2026-09")]
    Q = {r["quarter"]: r for r in q.quarterly(rows, 7)}
    assert set(Q) == {"2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1"} and Q["2026-Q1"]["first"] == "2026-07"
    c = q.compare("2026-Q1", q.quarterly(rows, 7), [], monthly=rows, ys=7)
    assert c["year_ago"]["quarter"] == "2025-Q1" and c["previous"]["quarter"] == "2025-Q4"


def test_movers_net_worth_and_the_look_ahead():
    rows = [mrow(m, 6000, 3000) for m in months("2025-01", "2026-06")]
    cats = [{"quarter": "2026-Q2", "category": "Food", "amount": 2700.0, "amount_real": 2700.0}, {"quarter": "2026-Q1", "category": "Food", "amount": 2400.0, "amount_real": 2400.0},
            {"quarter": "2026-Q2", "category": "Fun", "amount": 30.0, "amount_real": 30.0}, {"quarter": "2026-Q1", "category": "Fun", "amount": 3.0, "amount_real": 3.0}]
    nw = [{"m": "2026-03", "net_worth": 100000}, {"m": "2026-06", "net_worth": 104500}]
    cu = [{"m": "2026-08", "kind": "yearly_bill", "label": "x"}, {"m": "2026-12", "kind": "event", "label": "y"}]
    rows[-12] = mrow("2025-07", 9000, 3000)   # a July with a bonus: next July's expectation keeps it
    c = q.compare("2026-Q2", q.quarterly(rows), cats, nw, None, cu, rows)
    m = c["movers_previous"]
    assert m["by_dollars"][0] == {"category": "Food", "per_month": 900.0, "was": 800.0, "change": 100.0, "pct": 0.125, "new": False, "also_last_year": False}
    assert all(r["category"] != "Fun" for r in m["by_pct"])   # a $1-a-month line doubling is not a mover by %
    assert c["net_worth_change"] == 4500
    assert c["next"]["quarter"] == "2026-Q3" and c["next"]["expected"]["income"] == (6000 * 2 + 9000) / 3   # a month
    assert [x["m"] for x in c["next"]["coming_up"]] == ["2026-08"]


def test_the_build_emits_quarterly_cat_quarter_and_the_review(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0
    data = client.get("/api/datasets").json()
    Q = {r["quarter"]: r for r in data["quarterly"]}
    assert set(Q) == {"2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"}
    assert round(sum(r["income"] for r in Q.values()), 2) == round(sum(r["income"] for r in data["monthly"]), 2)
    (rv,) = data["quarter_review"]
    assert rv["quarter"] == "2026-Q2" and rv["previous"]["quarter"] == "2026-Q1" and rv["year_ago"]["quarter"] == "2025-Q2"
    assert rv["next"]["quarter"] == "2026-Q3" and {c["kind"] for c in rv["next"]["coming_up"]} >= {"event", "tax_deadline"}
    assert data["cat_quarter"] and all(set(r) == {"quarter", "category", "amount", "amount_real"} for r in data["cat_quarter"])


@pytest.fixture
def sample(client, reset_db):
    from pipeline import import_exports
    reset_db(SAMPLE)
    assert import_exports.main([str(SAMPLE)]) == 0


def test_check_in_tab_review_and_cli(client, sample, capsys):
    from app import admin, checkin, db, mcp_endpoint, review
    page = client.get("/checkin/2026-06").text   # June ends calendar Q2
    step = page[page.find('id="step-quarter"'):]
    assert "The quarter" in page and "2026 Q2" in step and "2026 Q1" in step and "2025 Q2" in step and "data-view" in page
    with db.pool.connection() as conn:
        assert "quarter" not in {s["key"] for s in checkin.overview(conn, "2026-05")["steps"]}   # not a quarter's end
        ctx = review.build_context(conn, "2026-06")
        assert ctx["quarter"]["quarter"] == "2026-Q2" and ctx["quarter"]["year_ago"]["quarter"] == "2025-Q2" and "Lembas" not in str(ctx["quarter"])
        assert "quarter" not in review.build_context(conn, "2026-05")
    assert "The quarter" in review.SYSTEM
    html = client.get("/").text
    assert 'id="tab-quarter"' in html and 'data-draw="q-compare"' in html
    assert {"quarterly", "cat_quarter", "quarter_review"} <= set(mcp_endpoint.DATASETS)
    assert admin.main(["quarter"]) == 0
    out = capsys.readouterr()
    assert "quarter\t2026-Q2" in out.out and "quarters equal the sum of their months\tpass" in out.out and "from the last build" in out.err
    assert admin.main(["quarter", "2025-Q1"]) == 0 and "vs a year before" in capsys.readouterr().out
    assert admin.main(["quarter", "Q3"]) == 2


def test_cli_works_on_an_install_built_before_quarters(client, sample, capsys):
    """Check B runs this branch's report against an older install: no quarterly datasets there. It works them out from the monthly
    figures in a read-only transaction and saves nothing."""
    from app import admin, db
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM datasets WHERE name IN ('quarterly', 'cat_quarter', 'quarter_review', 'coming_up')"); conn.commit()
    assert admin.main(["quarter", "2026-Q2"]) == 0
    out = capsys.readouterr()
    assert "worked out now" in out.err and "coming up: yearly bills only" in out.err   # 0.5.0 has no coming_up (homelab check) and "check\tquarters equal the sum of their months\tpass" in out.out
    with db.pool.connection() as conn:
        assert conn.execute("SELECT 1 FROM datasets WHERE name='quarterly'").fetchone() is None


def test_movers_leave_out_debt_and_savings_mark_last_years_pattern_and_net_turning_negative():
    """From the homelab check (#147): debt and savings categories repeated the debt line; a yearly bill looked like a habit change;
    net across a change of sign read "-358%"; a category with no base read "()"."""
    rows = [mrow(m, 6000, 3000) for m in months("2025-01", "2025-12")] + [mrow(m, 6000, 3000) for m in months("2026-01", "2026-03")] \
        + [mrow(m, 3000, 3500) for m in months("2026-04", "2026-06")]
    cats = []
    for quarter, ins, debt, gifts in (("2025-Q1", 300, 500, 0), ("2025-Q2", 1500, 500, 0), ("2026-Q1", 300, 500, 0), ("2026-Q2", 1500, 1400, 90)):
        cats += [{"quarter": quarter, "category": "Insurance", "amount": ins, "amount_real": ins}, {"quarter": quarter, "category": "Debt", "amount": debt, "amount_real": debt},
                 {"quarter": quarter, "category": "Gifts", "amount": gifts, "amount_real": gifts}]
    c = q.compare("2026-Q2", q.quarterly(rows), cats, monthly=rows, set_apart={"Debt"})
    m = c["movers_previous"]
    assert [x["category"] for x in m["by_dollars"]] == ["Insurance", "Gifts"] and [x["category"] for x in m["set_apart"]] == ["Debt"]
    ins, gifts = m["by_dollars"]
    assert ins["also_last_year"] and not gifts["also_last_year"] and gifts["new"] and gifts["pct"] is None
    assert c["previous"]["net_pct"] is None and c["previous"]["net_turned"] == "negative" and c["previous"]["net_change"] == -600 - 2900
    assert c["previous"]["income_pct"] == -0.5 and c["previous"]["income_turned"] is None
