"""Month-close review: send the closed month's figures to the model, store 5–7 titled sections.

Only derived numbers go out (planned vs spent per envelope, category totals, the ledger's monthly totals, goals). No raw
transactions, merchants or notes. The prompt is stored alongside the result so a review can always be audited.
"""
from __future__ import annotations
import json
from statistics import mean

from . import review_providers as providers

SYSTEM = (
    "You write the month-close review for a household budget dashboard. You are given the closed budget month, the month before it, "
    "trailing-twelve-month context and the household's standing targets. Write 5 to 7 sections as JSON: "
    '{"sections":[{"order":1,"heading":"...","text":"..."}]}. Headings are short; each text is one tight paragraph of plain prose, '
    "no bullet points, no markdown. Every number must come from the data given; round to whole dollars. Say \"EveryDollar's view\" when "
    "quoting planned/spent and \"the ledger's view\" when quoting the cleaned totals — they differ because the ledger removes transfers. "
    "Required sections, in this order: the month in one line; against the prior month; against the trailing twelve; envelopes most over "
    "and under plan (name plan amounts that look wrong-sized); where the standing ratios stand (giving vs floor/target, joy savings vs target, "
    "housing+utilities vs cap) and whether this month moved them; one trend worth watching; what to change in next month's plan. "
    "If the month had three paychecks, say so and do not read it as a raise. Return only the JSON object."
)


def _month_before(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def build_context(conn, month: str) -> dict:
    ds = {r["name"]: r["rows"] for r in conn.execute("SELECT name, rows FROM datasets WHERE name IN ('monthly','cat_month','budget_items','goals','people')").fetchall()}
    monthly = {r["m"]: r for r in ds.get("monthly", [])}
    months = sorted(monthly)
    if month not in monthly:
        raise ValueError(f"{month} is not a built month")
    prev = _month_before(month)
    idx = months.index(month)
    t12 = months[max(0, idx - 12):idx]
    cat = {}
    for r in ds.get("cat_month", []):
        cat.setdefault(r["m"], {})[r["category"]] = r["amount"]
    t12_cat = {}
    for c in {c for m in t12 for c in cat.get(m, {})}:
        t12_cat[c] = round(mean(cat.get(m, {}).get(c, 0) for m in t12), 2) if t12 else None
    items = [{k: r[k] for k in ("group", "item", "category", "planned", "actual")} for r in ds.get("budget_items", []) if r["m"] == month]
    paychecks = sum(1 for r in ds.get("budget_items", []) if r["m"] == month and r["category"] == "Income" and r["actual"] and r["canonical"] == "Paycheck")
    goals = {g["key"]: {"value": g["value"], "unit": g["unit"], "label": g["label"]} for g in ds.get("goals", [])}
    return {
        "month": month, "prior_month": prev,
        "ledger_totals": {"month": monthly[month], "prior": monthly.get(prev), "trailing12_avg": {k: round(mean(monthly[m][k] for m in t12), 2) for k in ("income", "spend", "debt", "net")} if t12 else None},
        "categories": {"month": cat.get(month, {}), "prior": cat.get(prev, {}), "trailing12_avg": t12_cat},
        "envelopes_planned_vs_actual": items,
        "paycheck_envelopes_with_money": paychecks,
        "goals": goals,
    }


def generate(conn, month: str) -> dict:
    prov = providers.resolve(conn)
    if (why := prov.problem()):
        raise RuntimeError(f"no review provider is configured: {why}")
    ctx = build_context(conn, month)
    prompt = json.dumps(ctx, separators=(",", ":"))
    sections = providers.parse_sections(prov.complete(SYSTEM, prompt))
    conn.execute(
        "INSERT INTO reviews (month, model, sections, prompt) VALUES (%s,%s,%s,%s) ON CONFLICT (month) DO UPDATE SET generated_at=now(), model=EXCLUDED.model, sections=EXCLUDED.sections, prompt=EXCLUDED.prompt",
        (month, prov.label, json.dumps(sections), prompt),
    )
    rows = conn.execute("SELECT month, sections FROM reviews ORDER BY month").fetchall()
    review_rows = [dict(m=r["month"], **sec) for r in rows for sec in r["sections"]]
    conn.execute("INSERT INTO datasets (name, rows, row_count) VALUES ('review',%s,%s) ON CONFLICT (name) DO UPDATE SET rows=EXCLUDED.rows, row_count=EXCLUDED.row_count, built_at=now()", (json.dumps(review_rows), len(review_rows)))
    conn.commit()
    return {"month": month, "sections": sections}
