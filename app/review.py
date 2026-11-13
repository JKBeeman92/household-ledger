"""Month-close review: send the closed month's figures to the model, store 5–7 titled sections.

Only derived numbers go out (planned vs spent per envelope, category totals, the ledger's monthly totals, goals). No raw
transactions, merchants or notes. The prompt is stored alongside the result so a review can always be audited.
"""
from __future__ import annotations
import json, logging
from statistics import mean

from . import db, hooks, review_providers as providers

log = logging.getLogger("ledger")

SETTING = "review_instructions"
SYSTEM = (
    "You write the month-close review for a household budget dashboard. You are given the closed budget month, the month before it, "
    "trailing-twelve-month context and the household's standing targets. Write 5 to 7 sections as JSON: "
    '{"sections":[{"order":1,"heading":"...","text":"..."}]}. Headings are short; each text is one tight paragraph of plain prose, '
    "no bullet points, no markdown. Every number must come from the data given; round to whole dollars. Say \"the budget tool's view\" when "
    "quoting planned/spent and \"the ledger's view\" when quoting the cleaned totals — they differ because the ledger removes transfers. "
    "Required sections, in this order: the month in one line; against the prior month; against the trailing twelve; envelopes most over "
    "and under plan (name plan amounts that look wrong-sized); where the standing ratios stand (giving vs floor/target, savings vs target, "
    "housing+utilities vs cap) and whether this month moved them; one trend worth watching; what to change in next month's plan. "
    "If the month had three paychecks, say so and do not read it as a raise. "
    "The data may also carry last month's review, the household's own decisions with how each went, life events and goal confirmations: "
    "comment on the household's decisions (kept, partly, not kept, or still waiting for the month's files), not on a suggestion of your own, "
    "and name a life event (a move, a job change) rather than inferring it. "
    "When the data carries `quarter` (the month ends a quarter), add one more section, last, headed \"The quarter\": the quarter against "
    "the one before and the same quarter a year before, a month at a time (say so when a quarter is partial or not loaded), the "
    "categories that moved most, and what falls in the next quarter. Return only the JSON object."
)


def instructions(conn) -> str:
    """The effective system prompt: the household's saved text, else the built-in SYSTEM. One source for every provider (and an MCP tool)."""
    saved = db.get_setting(conn, SETTING, None)
    return saved if isinstance(saved, str) and saved.strip() else SYSTEM


def _month_before(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


SECTION_CHARS = 1200   # per stored section; keeps last month's review from dominating the prompt
CONTINUITY_NOTE = ("prior_review is last month's stored review, for continuity. decisions are what the household itself decided at earlier "
                   "check-ins, each with the months it was in force, the app's own check (auto_verdict, from the budget: pending means the "
                   "month's files are not in yet, unknown that it cannot be checked) and the household's verdict when given: comment on these, "
                   "the household's decisions, rather than on any suggestion of your own. life_events are "
                   "the household's own notes for this and the prior month, each with its kind (job, move, family, health, purchase, debt, other) and person when given: name them rather than inferring. goal_confirmations are the answers from the month-close checklist.")


def continuity(conn, month: str, prev: str) -> dict:
    """Prior review, life events and confirmed goals: derived text and household-entered labels only, never transactions, merchants or notes."""
    row = conn.execute("SELECT sections FROM reviews WHERE month=%s", (prev,)).fetchone()
    prior = [{"heading": s["heading"], "text": s["text"][:SECTION_CHARS]} for s in row["sections"]] if row else None
    events = {m: [{"label": r["label"], **{k: r[k] for k in ("kind", "person") if r[k]}}   # kind and person when set (#109)
                  for r in conn.execute("SELECT label, kind, person FROM life_events WHERE month=%s ORDER BY label", (m,)).fetchall()] for m in (prev, month)}
    goals = [{"key": r["key"], "label": r["label"], "value": float(r["value"]) if r["value"] is not None else None, "unit": r["unit"], "confirmed": r["confirmed_at"].date().isoformat()}
             for r in conn.execute("SELECT key, label, value, unit, confirmed_at FROM goals WHERE confirmed_at IS NOT NULL ORDER BY key").fetchall()]
    return {"note": CONTINUITY_NOTE, "prior_review": prior, "life_events": events, "goal_confirmations": goals, "decisions": decisions_context(conn, month)}


def decisions_context(conn, month: str) -> list[dict]:
    """The household's decisions in force this month and the month before, with both verdicts (#155). The text is the household's
    own words, as a life event's label is; the private note never leaves."""
    r = conn.execute("SELECT rows FROM datasets WHERE name='decisions'").fetchone()
    rows = [x for x in (r["rows"] if r else []) if x["m"] in (_month_before(month), month)]
    return [{k: x[k] for k in ("m", "made_in", "text", "kind", "target", "auto_verdict", "auto_figure", "verdict")} for x in rows]


PAYCHECK_ITEMS_DEFAULT = ["Paycheck"]   # role default: build_ledger.RULES["paycheck_items"]


def build_context(conn, month: str) -> dict:
    ds = {r["name"]: r["rows"] for r in conn.execute("SELECT name, rows FROM datasets WHERE name IN ('monthly','cat_month','budget_items','goals','people','balance_totals','debt_to_income','net_worth','emergency_fund','coming_up','tax')").fetchall()}
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
    pay_rule = (db.get_setting(conn, "rules", {}) or {}).get("paycheck_items")   # null = the default, [] = none, as in build_ledger
    pay_items = set(pay_rule if pay_rule is not None else PAYCHECK_ITEMS_DEFAULT)
    paychecks = sum(1 for r in ds.get("budget_items", []) if r["m"] == month and r["flow"] == "income" and r["actual"] and r["canonical"] in pay_items)
    goals = {g["key"]: {"value": g["value"], "unit": g["unit"], "label": g["label"]} for g in ds.get("goals", [])}
    return {
        "month": month, "prior_month": prev,
        "savings_rate": savings_rates(monthly, month, t12 + [month]),
        "ledger_totals": {"month": monthly[month], "prior": monthly.get(prev), "trailing12_avg": {k: round(mean(monthly[m][k] for m in t12), 2) for k in ("income", "spend", "debt", "net")} if t12 else None},
        "categories": {"month": cat.get(month, {}), "prior": cat.get(prev, {}), "trailing12_avg": t12_cat},
        "envelopes_planned_vs_actual": items,
        "paycheck_envelopes_with_money": paychecks,
        "goals": goals,
        **({"balances": balances_context(ds.get("balance_totals", []), month, prev)} if ds.get("balance_totals") else {}),
        **({"debt_to_income": dti_context(ds.get("debt_to_income", []), goals, month)} if ds.get("debt_to_income") else {}),
        **({"net_worth": nw_context(ds.get("net_worth", []), month)} if any(r["m"] == month for r in ds.get("net_worth", [])) else {}),
        **({"emergency_fund": ef_context(ds.get("emergency_fund", []), goals, month)} if any(r["m"] == month and r["months"] is not None for r in ds.get("emergency_fund", [])) else {}),
        **({"estimated_tax": {k: (ds.get("tax") or [{}])[0].get(k) for k in ("year", "contract_income", "pct", "expected", "set_aside", "short", "quarter_income", "quarter_expected", "next_deadline", "next_label")}}
           if ds.get("tax") and ds["tax"][0].get("m") == month else {}),   # #153
        **({"quarter": qc} if (qc := quarter_context(conn, month)) else {}),   # #147: one review a month, extended at a quarter's end
        **({"coming_up": coming_up_context(ds.get("coming_up", []), month)} if coming_up_context(ds.get("coming_up", []), month) else {}),
        "continuity": continuity(conn, month, prev),
    }


QUARTER_NOTE = ("This month ends a budget quarter. Figures are a month's average, so a partial quarter compares fairly; previous and year_ago "
                "are null when that quarter is not loaded (say so; never read it as zero). Movers are living-spend categories by change in monthly "
                "spend; debt and savings groups are in set_apart, not movers. also_last_year marks a mover the same quarter a year before moved "
                "the same way (a yearly bill or a season: say so, not a change in habit). A *_turned value means the figure changed sign; give "
                "the dollar change, not a percent. next.expected is a month's figures from the same months a year before, an estimate.")


def quarter_context(conn, month: str) -> dict | None:
    """quarter_review (#147) for the review of a quarter's last month: only derived figures, no account names."""
    from .checkin import mode_of
    if mode_of(conn, month) not in ("quarter", "year"):
        return None
    r = conn.execute("SELECT rows FROM datasets WHERE name='quarter_review'").fetchone()
    q = (r["rows"] or [None])[0] if r and r["rows"] else None
    if not q or q.get("last") != month:
        return None
    nxt = {**q["next"], "coming_up": [{"m": c["m"], "kind": c["kind"], "label": c.get("public_label") or c["label"], "amount": c.get("amount")} for c in q["next"]["coming_up"]]}
    return {"note": QUARTER_NOTE, **{k: q[k] for k in ("quarter", "first", "last", "months", "partial", "this", "previous", "year_ago",
                                                       "movers_previous", "movers_year_ago", "net_worth_change", "debt_change")}, "next": nxt}


COMING_UP_NOTE = ("What falls due in the three budget months after this one: estimated payoffs at last month's payment and life events "
                  "already on the calendar. Labels name a debt by its type, not its account.")


def coming_up_context(rows: list[dict], month: str, ahead: int = 3) -> dict | None:
    """coming_up (#144) for the review: the next `ahead` months, with public labels (no account names, the privacy line)."""
    y, m = int(month[:4]), int(month[5:]) + ahead
    until = f"{y + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}"
    items = [{"m": r["m"], "kind": r["kind"], "label": r["public_label"], "amount": r["amount"], "source": r["source"]}
             for r in rows if month < r["m"] <= until]
    return {"note": COMING_UP_NOTE, "items": items} if items else None


BALANCES_NOTE = ("Month-end balances by account type (no account names): debts are what is owed, assets what is held, over the accounts "
                 "counted in net worth. estimated counts debts with no balance typed for the month, worked out from the one before and the payments since.")


def balances_context(totals: list[dict], month: str, prev: str) -> dict:
    """Balances for the review (#143): totals by kind and type for the month and the one before; never an account name."""
    pick = lambda m: [{k: r[k] for k in ("kind", "type", "amount", "accounts", "estimated")} for r in totals if r["m"] == m]
    return {"note": BALANCES_NOTE, "month": pick(month), "prior": pick(prev)}


def prompt_for(conn, month: str) -> str:
    return json.dumps(build_context(conn, month), separators=(",", ":"))


def estimate_tokens(conn, month: str) -> int:
    """Rough input size (about four characters a token) of what a generate call would send for `month`."""
    return -(-(len(instructions(conn)) + len(prompt_for(conn, month))) // 4)


def refresh_dataset(conn) -> None:
    rows = conn.execute("SELECT month, sections FROM reviews ORDER BY month").fetchall()
    review_rows = [dict(m=r["month"], **sec) for r in rows for sec in r["sections"]]
    conn.execute("INSERT INTO datasets (name, rows, row_count) VALUES ('review',%s,%s) ON CONFLICT (name) DO UPDATE SET rows=EXCLUDED.rows, row_count=EXCLUDED.row_count, built_at=now()", (json.dumps(review_rows), len(review_rows)))


def _excerpt(text: str, n: int = 400) -> str:
    """Start and end of a reply for the log: enough to see prose instead of JSON, a cut-off tail or a stray quote."""
    t = text.replace("\n", "\\n")
    return repr(t) if len(t) <= 2 * n else repr(t[:n]) + " … " + repr(t[-n:])


def generate(conn, month: str) -> dict:
    prov = providers.resolve(conn)
    if (why := prov.problem()):
        raise RuntimeError(f"no review provider is configured: {why}")
    prompt = prompt_for(conn, month)
    try:
        reply = prov.complete(instructions(conn), prompt)
        sections = providers.parse_sections(reply)
    except (providers.ReplyCutOff, ValueError) as e:
        text = e.text if isinstance(e, providers.ReplyCutOff) else reply
        log.warning("review %s from %s failed: %s; reply was %d chars: %s", month, prov.label, e, len(text), _excerpt(text))
        raise
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


# ---------------------------------------------------------------- background jobs (issue #124)
STALE_MINUTES = 30   # longer than any provider wait (REVIEW_MAX_TOKENS / 40 s, 400 s by default): a job still "running" after this was cut off by a restart


def start_job(conn, month: str, who: str) -> int | None:
    """Queue a review of `month`; None when one is already queued or running for it (the unique index says so)."""
    try:
        row = conn.execute("INSERT INTO review_jobs (month, state, requested_by, provider) VALUES (%s, 'queued', %s, %s) RETURNING id",
                           (month, who, providers.resolve(conn).label)).fetchone()
        conn.commit(); return row["id"]
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        if "review_jobs_one_active" in str(e):
            return None
        raise


def run_job(job_id: int) -> None:
    """Body of the background task: write the review on a connection of its own and record how it went. Never raises."""
    try:
        with db.pool.connection() as conn:
            month = conn.execute("UPDATE review_jobs SET state='running', started_at=now() WHERE id=%s RETURNING month", (job_id,)).fetchone()["month"]
            conn.commit()
            try:
                res = generate(conn, month)
                conn.execute("UPDATE review_jobs SET state='stored', finished_at=now(), sections=%s WHERE id=%s", (len(res["sections"]), job_id))
            except Exception as e:  # noqa: BLE001  generate() logs the reply excerpt; the page shows the reason
                conn.rollback()
                conn.execute("UPDATE review_jobs SET state='failed', finished_at=now(), error=%s WHERE id=%s", (str(e)[:800], job_id))
            conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("review job %s could not be recorded", job_id)


def latest_job(conn) -> dict | None:
    """The newest review job, for Close a month; a job cut off by a restart is closed as interrupted."""
    conn.execute("UPDATE review_jobs SET state='failed', finished_at=now(), error='interrupted: the app restarted while the review was being written; write it again' "
                 "WHERE state IN ('queued', 'running') AND created_at < now() - make_interval(mins => %s)", (STALE_MINUTES,))
    conn.commit()
    row = conn.execute("SELECT id, month, state, provider, created_at, started_at, finished_at, sections, error FROM review_jobs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    job = dict(row)
    latest = conn.execute("SELECT max(budget_month) m FROM source_files").fetchone()["m"]
    job["where"] = "the dashboard's Last month tab" if job["month"] == latest else "Reviews → Past reviews"   # the line already names the month
    from .checkin import mode_of   # a quarter's last month carries one more section, "The quarter" (#147)
    lo, hi = (6, 8) if mode_of(conn, job["month"]) in ("quarter", "year") else (5, 7)
    job["odd_count"] = job["state"] == "stored" and job["sections"] is not None and not lo <= job["sections"] <= hi
    job["expected_sections"] = f"{lo} to {hi}"
    return job


DTI_NOTE = ("Debt-to-income (#145): debt payments, and housing payments (the housing categories plus mortgage-type debt), as a share of gross "
            "pay (the stated salary / 12) and of take-home, for the month and the trailing twelve. null means no gross pay is recorded. "
            "Caps are the household's own (dti_cap, housing_dti_cap in goals); with none set, do not judge the ratio against a lender's rule of thumb.")


def dti_context(rows: list[dict], goals: dict, month: str) -> dict | None:
    r = next((x for x in rows if x["m"] == month), None)
    if not r:
        return None
    keep = ("dti", "housing_dti", "dti_takehome", "housing_takehome", "dti_12", "housing_dti_12")
    return {"note": DTI_NOTE, **{k: r[k] for k in keep},
            "caps": {k: goals[k]["value"] for k in ("dti_cap", "housing_dti_cap") if k in goals and goals[k]["value"] is not None}}


NW_NOTE = ("Net worth (#146): assets less debts over the household's accounts, at the month-end. change splits into you_did (the month's "
           "income less living spend: kept plus paid on debts) and happened (investments and home estimates moving, interest, money in "
           "accounts not tracked); added is accounts new this month and closed accounts retired, so neither reads as progress. estimated "
           "counts debts with no balance entered. Totals only: no account is named.")


def nw_context(rows: list[dict], month: str) -> dict:
    r = next(x for x in rows if x["m"] == month)
    keep = ("assets", "debts", "net_worth", "net_worth_ex_home", "change", "you_did", "happened", "added", "closed", "estimated",
            "change_12", "you_did_12", "happened_12")
    return {"note": NW_NOTE, **{k: r[k] for k in keep}}


EF_NOTE = ("Emergency fund (#150): months of outgo (living spend plus debt payments, averaged over the trailing twelve months) that the "
           "accounts marked emergency would cover. target is the household's own emergency_months, when set.")


def ef_context(rows: list[dict], goals: dict, month: str) -> dict:
    r = next(x for x in rows if x["m"] == month)
    t = goals.get("emergency_months", {}).get("value")
    return {"note": EF_NOTE, **{k: r[k] for k in ("months", "change", "emergency", "outgo", "outgo_months")}, "target": t}


def savings_rates(monthly: dict, month: str, window: list[str]) -> dict:
    """Net over income as the budget sees it (take-home), and with the stated pre-tax saving (#152) added to both sides; month and
    the twelve months to it. including_pretax is None when no pre-tax saving is stated."""
    def rate(ms, pre):
        inc, net = sum(monthly[m]["income"] for m in ms), sum(monthly[m]["net"] for m in ms)
        p = sum(monthly[m].get("pretax") or 0 for m in ms) if pre else 0
        return round((net + p) / (inc + p), 4) if inc + p else None
    has = all(monthly[m].get("pretax") is not None for m in window)
    return {"note": "take_home: net / income as they reach the budget. including_pretax: (net + pre-tax saving) / (income + pre-tax saving).",
            "month": {"take_home": rate([month], False), "including_pretax": rate([month], True) if has else None},
            "trailing12": {"take_home": rate(window[-12:], False), "including_pretax": rate(window[-12:], True) if has else None}}
