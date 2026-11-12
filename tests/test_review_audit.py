"""Review cost guard, audit list and hand-editing. The provider is stubbed; nothing touches the network."""
from __future__ import annotations
import json
from markupsafe import escape

import pytest

from conftest import SAMPLE
from app import db, review, review_providers as rp
from test_review_providers import Resp, SECTIONS, cfg, http  # noqa: F401  (fixtures)


@pytest.fixture
def month(client, reset_db, cfg, http):  # noqa: F811
    reset_db(SAMPLE)
    client.post("/upload", files=[("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(p for p in SAMPLE.glob("*.csv") if __import__("pipeline.filenames", fromlist=["x"]).is_export(p.name))])
    cfg(anthropic_api_key="k")
    http[1]["v"] = Resp({"content": [{"type": "text", "text": json.dumps(SECTIONS)}]})
    with db.pool.connection() as conn:
        return conn.execute("SELECT max(budget_month) m FROM source_files").fetchone()["m"]


def test_generate_form_goes_to_confirmation_and_post_needs_confirm(client, month, http):
    assert 'action="/reviews/confirm"' in client.get("/upload").text
    r = client.post("/review/generate", data={"month": month})
    assert r.status_code == 303 and r.headers["location"] == f"/reviews/confirm?month={month}"
    assert http[0] == []   # no provider call without confirmation


def test_confirmation_shows_month_and_token_estimate_then_generates(client, month, http):
    page = client.get(f"/reviews/confirm?month={month}").text
    with db.pool.connection() as conn:
        tokens = review.estimate_tokens(conn, month)
    assert month in page and f"{tokens:,}" in page and tokens > 100 and "Anthropic (" in page and http[0] == []
    assert client.post("/review/generate", data={"month": month, "confirm": "yes"}).status_code == 303
    assert len(http[0]) == 1
    assert "already exists" in client.get(f"/reviews/confirm?month={month}").text


def test_confirmation_unconfigured_and_bad_month(client, month, cfg):
    cfg(anthropic_api_key="")
    page = client.get(f"/reviews/confirm?month={month}").text
    assert "Not configured" in page and "disabled" in page
    assert client.get("/reviews/confirm?month=nope").status_code == 303
    assert "Upload that month" in client.get("/reviews/confirm?month=1999-01").text


def test_history_lists_prompt_and_model(client, month):
    client.post("/review/generate", data={"month": month, "confirm": "yes"})
    page = client.get("/reviews/history").text
    assert month in page and "anthropic" in page and "ledger_totals" in page and "Calm." in page


def test_edit_marks_hand_edited_and_refreshes_dataset(client, month):
    client.post("/review/generate", data={"month": month, "confirm": "yes"})
    assert "· hand-edited" not in client.get("/reviews/history").text
    assert "Calm." in client.get(f"/reviews/edit/{month}").text
    r = client.post(f"/reviews/edit/{month}", data={"heading": ["The month", "Plan", ""], "text": ["Rather calm, actually.", "Trim.", ""]})
    assert r.status_code == 303
    with db.pool.connection() as conn:
        row = conn.execute("SELECT sections, hand_edited_at, prompt FROM reviews WHERE month=%s", (month,)).fetchone()
        ds = conn.execute("SELECT rows FROM datasets WHERE name='review'").fetchone()["rows"]
    assert row["hand_edited_at"] and row["prompt"] and [s["order"] for s in row["sections"]] == [1, 2]
    assert [d["text"] for d in ds if d["m"] == month] == ["Rather calm, actually.", "Trim."]
    assert "· hand-edited" in client.get("/reviews/history").text
    assert "discards your edits" in client.get(f"/reviews/confirm?month={month}").text
    client.post("/review/generate", data={"month": month, "confirm": "yes"})   # regenerating clears the mark
    with db.pool.connection() as conn:
        assert conn.execute("SELECT hand_edited_at FROM reviews WHERE month=%s", (month,)).fetchone()["hand_edited_at"] is None


def test_edit_rejects_incomplete_and_unknown(client, month):
    client.post("/review/generate", data={"month": month, "confirm": "yes"})
    for data in ({"heading": ["Only heading"], "text": [""]}, {"heading": [""], "text": [""]}):
        assert "Not saved" in client.get(client.post(f"/reviews/edit/{month}", data=data).headers["location"]).text
    with db.pool.connection() as conn:
        assert conn.execute("SELECT hand_edited_at FROM reviews WHERE month=%s", (month,)).fetchone()["hand_edited_at"] is None
    assert client.get("/reviews/edit/1999-01").status_code == 404
    assert client.post("/reviews/edit/..%2Fx", data={"heading": ["a"], "text": ["b"]}).status_code == 404


def test_audit_routes_need_editor(client, reset_db, monkeypatch):
    from app.config import settings
    import dataclasses, app.auth as auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, auth_disabled=False))
    for path in ("/reviews/history", "/reviews/confirm?month=2026-01", "/reviews/edit/2026-01"):
        assert client.get(path).status_code == 307


def test_instructions_default_edit_reset_and_used_by_provider(client, month, http):
    with db.pool.connection() as conn:
        assert review.instructions(conn) == review.SYSTEM
    assert review.SYSTEM[:40] in client.get("/reviews/prompt").text.replace("&#34;", '"')
    assert client.post("/reviews/prompt", data={"instructions": "Emphasise debt payoff.\r\n"}).status_code == 303
    with db.pool.connection() as conn:
        assert review.instructions(conn) == "Emphasise debt payoff."
    assert "(customised)" in client.get("/reviews/prompt").text
    client.post("/review/generate", data={"month": month, "confirm": "yes"})
    assert "Emphasise debt payoff." in json.dumps(http[0][0], default=str)
    client.post("/reviews/prompt/reset")
    with db.pool.connection() as conn:
        assert review.instructions(conn) == review.SYSTEM and db.get_setting(conn, review.SETTING) is None


def test_blank_or_builtin_text_clears_override_and_long_is_rejected(client, month):
    client.post("/reviews/prompt", data={"instructions": "x"})
    client.post("/reviews/prompt", data={"instructions": "  "})
    with db.pool.connection() as conn:
        assert db.get_setting(conn, review.SETTING) is None
    assert "Not saved" in client.get(client.post("/reviews/prompt", data={"instructions": "x" * 20001}).headers["location"]).text


def test_prompt_preview_is_exact_context_without_provider_call(client, month, http):
    with db.pool.connection() as conn:
        exact = review.prompt_for(conn, month)
    page = client.get(f"/reviews/prompt?month={month}").text
    assert str(escape(exact)) in page and http[0] == []
    assert "Upload that month" in client.get("/reviews/prompt?month=1999-01").text
    assert "Pick a budget month" in client.get("/reviews/prompt?month=bad").text


def test_context_carries_prior_review_life_events_and_goal_confirmations(client, month):
    prev = review._month_before(month)
    secret = {"transactions", "merchant", "memo"}
    with db.pool.connection() as conn:
        base = review.build_context(conn, month)["continuity"]
        assert base["prior_review"] is None and base["life_events"] == {prev: [], month: []}
        conn.execute("INSERT INTO reviews (month, model, sections, prompt) VALUES (%s,'m',%s,'p')", (prev, json.dumps([{"order": 1, "heading": "Plan", "text": "Cut dining out by $50. " + "x" * 3000}])))
        conn.execute("INSERT INTO life_events (month, label) VALUES (%s,'Moved to Bywater'),(%s,'New job at the mill'),('1999-01','Old news')", (prev, month))
        conn.execute("INSERT INTO goals (key, value, unit, label, confirmed_at) VALUES ('giving_floor', 10, '%', 'Giving floor', '2026-03-04T10:00:00Z') ON CONFLICT (key) DO UPDATE SET value=10, unit='%', label='Giving floor', confirmed_at='2026-03-04T10:00:00Z'")
        conn.execute("INSERT INTO goals (key, value, unit, label) VALUES ('unasked', 1, '', 'Never confirmed') ON CONFLICT (key) DO NOTHING")
        conn.commit()
        ctx = review.build_context(conn, month)
        sent = review.prompt_for(conn, month)
    c = ctx["continuity"]
    assert c["prior_review"][0]["heading"] == "Plan" and c["prior_review"][0]["text"].startswith("Cut dining out") and len(c["prior_review"][0]["text"]) == review.SECTION_CHARS
    assert c["life_events"] == {prev: [{"label": "Moved to Bywater"}], month: [{"label": "New job at the mill"}]}
    assert [g["key"] for g in c["goal_confirmations"]] == ["giving_floor"]
    assert c["goal_confirmations"][0] == {"key": "giving_floor", "label": "Giving floor", "value": 10.0, "unit": "%", "confirmed": "2026-03-04"}
    assert "Old news" not in sent and not any(k in ctx for k in secret)
    assert "suggested change" in review.SYSTEM and "suggested change" in c["note"]


def test_customised_prompt_is_kept_and_preview_shows_continuity(client, month):
    client.post("/reviews/prompt", data={"instructions": "Emphasise debt payoff."})
    with db.pool.connection() as conn:
        conn.execute("INSERT INTO life_events (month, label) VALUES (%s,'Second breakfast habit')", (month,)); conn.commit()
        assert review.instructions(conn) == "Emphasise debt payoff."
    page = client.get(f"/reviews/prompt?month={month}").text
    assert "Second breakfast habit" in page and "prior_review" in page and "input tokens" in page


def test_confirm_page_shows_waiting_state_and_guards_a_second_send(client, month, http):
    """The POST waits for the whole reply: the page must say it is waiting, and a second click must not send (and pay for) it again."""
    t = client.get(f"/reviews/confirm?month={month}").text
    assert 'id="gen-form"' in t and 'id="gen-btn"' in t
    assert 'id="gen-status" class="muted" role="status" aria-live="polite" hidden' in t
    assert "form.dataset.sent" in t and "e.preventDefault()" in t and "nonce=" in t.split("form.dataset.sent")[0].rsplit("<script", 1)[1]
