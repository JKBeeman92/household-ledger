"""Month-close review: send the closed month's figures to the model, store 5–7 titled sections.

Only derived numbers go out (planned vs spent per envelope, category totals, the ledger's monthly totals, goals). No raw
transactions, merchants or notes. The prompt is stored alongside the result so a review can always be audited.
"""
from __future__ import annotations
import json
from statistics import mean

from . import db, hooks, review_providers as providers

SETTING = "review_instructions"
SYSTEM = (
    "You write the month-close review for a household budget dashboard. You are given the closed budget month, the month before it, "
    "trailing-twelve-month context and the household's standing targets. Write 5 to 7 sections as JSON: "
    '{"sections":[{"order":1,"heading":"...","text":"..."}]}. Headings are short; each text is one tight paragraph of plain prose, '
    "no bullet points, no markdown. Every number must come from the data given; round to whole dollars. Say \"EveryDollar's view\" when "
    "quoting planned/spent and \"the ledger's view\" when quoting the cleaned totals — they differ because the ledger removes transfers. "
    "Required sections, in this order: the month in one line; against the prior month; against the trailing twelve; envelopes most over "
    "and under plan (name plan amounts that look wrong-sized); where the standing ratios stand (giving vs floor/target, savings vs target, "
    "housing+utilities vs cap) and whether this month moved them; one trend worth watching; what to change in next month's plan. "
    "If the month had three paychecks, say so and do not read it as a raise. "
    "The data may also carry last month's review, life events and goal confirmations: say whether last month's suggested change was made, "
    "and name a life event (a move, a job change) rather than inferring it. Return only the JSON object."
)


def instructions(conn) -> str:
    """The effective system prompt: the household's saved text, else the built-in SYSTEM. One source for every provider (and an MCP tool)."""
    saved = db.get_setting(conn, SETTING, None)
    return saved if isinstance(saved, str) and saved.strip() else SYSTEM


def _month_before(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


SECTION_CHARS = 1200   # per stored section; keeps last month's review from dominating the prompt
CONTINUITY_NOTE = ("prior_review is last month's stored review: say whether its suggested change for this month was made. life_events are "
                   "the household's own notes for this and the prior month: name them rather than inferring. goal_confirmations are the answers from the month-close checklist.")


def continuity(conn, month: str, prev: str) -> dict:
    """Prior review, life events and confirmed goals: derived text and household-entered labels only, never transactions, merchants or notes."""
    row = conn.execute("SELECT sections FROM reviews WHERE month=%s", (prev,)).fetchone()
    prior = [{"heading": s["heading"], "text": s["text"][:SECTION_CHARS]} for s in row["sections"]] if row else None
    events = {m: [r["label"] for r in conn.execute("SELECT label FROM life_events WHERE month=%s ORDER BY label", (m,)).fetchall()] for m in (prev, month)}
    goals = [{"key": r["key"], "label": r["label"], "value": float(r["value"]) if r["value"] is not None else None, "unit": r["unit"], "confirmed": r["confirmed_at"].date().isoformat()}
             for r in conn.execute("SELECT key, label, value, unit, confirmed_at FROM goals WHERE confirmed_at IS NOT NULL ORDER BY key").fetchall()]
    return {"note": CONTINUITY_NOTE, "prior_review": prior, "life_events": events, "goal_confirmations": goals}


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
        "continuity": continuity(conn, month, prev),
    }


def prompt_for(conn, month: str) -> str:
    return json.dumps(build_context(conn, month), separators=(",", ":"))


def estimate_tokens(conn, month: str) -> int:
    """Rough input size (about four characters a token) of what a generate call would send for `month`."""
    return -(-(len(instructions(conn)) + len(prompt_for(conn, month))) // 4)


def refresh_dataset(conn) -> None:
    rows = conn.execute("SELECT month, sections FROM reviews ORDER BY month").fetchall()
    review_rows = [dict(m=r["month"], **sec) for r in rows for sec in r["sections"]]
    conn.execute("INSERT INTO datasets (name, rows, row_count) VALUES ('review',%s,%s) ON CONFLICT (name) DO UPDATE SET rows=EXCLUDED.rows, row_count=EXCLUDED.row_count, built_at=now()", (json.dumps(review_rows), len(review_rows)))


def generate(conn, month: str) -> dict:
    prov = providers.resolve(conn)
    if (why := prov.problem()):
        raise RuntimeError(f"no review provider is configured: {why}")
    prompt = prompt_for(conn, month)
    sections = providers.parse_sections(prov.complete(instructions(conn), prompt))
    conn.execute(
        "INSERT INTO reviews (month, model, sections, prompt, hand_edited_at) VALUES (%s,%s,%s,%s,NULL) ON CONFLICT (month) DO UPDATE SET generated_at=now(), model=EXCLUDED.model, sections=EXCLUDED.sections, prompt=EXCLUDED.prompt, hand_edited_at=NULL",
        (month, prov.label, json.dumps(sections), prompt),
    )
    refresh_dataset(conn)
    conn.commit()
    hooks.notify_async(conn, "review.stored", month, sections=len(sections))
    return {"month": month, "sections": sections}


def save_agent(conn, month: str, sections: list[dict], who: str) -> None:
    """Store a review an agent wrote (MCP save_review) through the same path as a generated one: the row says who wrote it, there is
    no prompt to keep, any hand-edit mark is cleared (the agent's text replaces the edited one), and the `review` dataset refreshes."""
    conn.execute(
        "INSERT INTO reviews (month, model, sections, prompt, hand_edited_at) VALUES (%s,%s,%s,%s,NULL) ON CONFLICT (month) DO UPDATE SET generated_at=now(), model=EXCLUDED.model, sections=EXCLUDED.sections, prompt=EXCLUDED.prompt, hand_edited_at=NULL",
        (month, who, json.dumps(sections), "(written by an agent over MCP; the agent saw the instructions and figures through its tools)"),
    )
    refresh_dataset(conn)
    conn.commit()
    hooks.notify_async(conn, "review.stored", month, sections=len(sections))
