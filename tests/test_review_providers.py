"""Review providers and the Reviews page. HTTP is stubbed; nothing here touches the network."""
from __future__ import annotations
import dataclasses, json

import httpx
import pytest

from conftest import SAMPLE
from app import review, review_providers as rp

SECTIONS = {"sections": [{"order": 1, "heading": "The month", "text": "Calm."}, {"order": 2, "heading": "Plan", "text": "Trim."}]}
EXPECTED = [{"order": 1, "heading": "The month", "text": "Calm."}, {"order": 2, "heading": "Plan", "text": "Trim."}]


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code, self.text = body, status, json.dumps(body)
        self.request = httpx.Request("POST", "http://x")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=self.request, response=self)

    def json(self):
        return self._b


@pytest.fixture
def cfg(monkeypatch):
    def set_(**kw):
        monkeypatch.setattr(rp, "settings", dataclasses.replace(rp.settings, **kw))
    set_(review_provider="anthropic", anthropic_api_key="", review_api_key="", review_base_url="", review_model_explicit="")
    return set_


@pytest.fixture
def http(monkeypatch):
    calls = []
    reply = {"v": Resp({})}
    monkeypatch.setattr(rp.httpx, "post", lambda url, **kw: (calls.append((url, kw)), reply["v"])[1])
    return calls, reply


def prov(name, **kw):
    d = dict(name=name, model="m1", base_url="", api_key="", max_tokens=3000); d.update(kw)
    return rp.Provider(**d)


def test_anthropic_request_shape_is_unchanged(http):
    calls, reply = http
    reply["v"] = Resp({"content": [{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}]})
    assert prov("anthropic", api_key="k").complete("S", "U") == "hello"
    url, kw = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert kw["headers"] == {"x-api-key": "k", "anthropic-version": "2023-06-01", "content-type": "application/json"}
    assert kw["json"] == {"model": "m1", "max_tokens": 3000, "system": "S", "messages": [{"role": "user", "content": "U"}]} and kw["timeout"] == 120


def test_openai_compatible_request_and_optional_key(http):
    calls, reply = http
    reply["v"] = Resp({"choices": [{"message": {"content": "hi"}}]})
    assert prov("openai", base_url="http://lm:1234/v1", api_key="k").complete("S", "U") == "hi"
    url, kw = calls[0]
    assert url == "http://lm:1234/v1/chat/completions" and kw["headers"]["authorization"] == "Bearer k"
    assert kw["json"]["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}] and kw["json"]["max_tokens"] == 3000
    prov("openai", base_url="http://lm:1234/v1").complete("S", "U")
    assert "authorization" not in calls[1][1]["headers"]


def test_ollama_request(http):
    calls, reply = http
    reply["v"] = Resp({"message": {"role": "assistant", "content": "yo"}})
    assert prov("ollama", base_url="http://ollama:11434").complete("S", "U") == "yo"
    url, kw = calls[0]
    assert url == "http://ollama:11434/api/chat" and kw["json"]["stream"] is False and kw["json"]["options"] == {"num_predict": 3000}


def test_gemini_request(http):
    calls, reply = http
    reply["v"] = Resp({"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]})
    assert prov("gemini", api_key="gk").complete("S", "U") == "ab"
    url, kw = calls[0]
    assert url.endswith("/models/m1:generateContent") and kw["headers"]["x-goog-api-key"] == "gk" and "gk" not in url
    assert kw["json"]["systemInstruction"] == {"parts": [{"text": "S"}]} and kw["json"]["generationConfig"] == {"maxOutputTokens": 3000}


def test_http_errors_are_short_and_keyless(http):
    _, reply = http
    reply["v"] = Resp({"error": "nope"}, 401)
    with pytest.raises(RuntimeError, match="openai returned HTTP 401"):
        prov("openai", base_url="http://x/v1", api_key="sekrit").complete("S", "U")
    reply["v"] = Resp({"unexpected": 1})
    with pytest.raises(RuntimeError, match="choices"):
        prov("openai", base_url="http://x/v1").complete("S", "U")


@pytest.mark.parametrize("text", [
    json.dumps(SECTIONS),
    "```json\n" + json.dumps(SECTIONS) + "\n```",
    "```\n" + json.dumps(SECTIONS) + "\n```\n",
    "Sure! Here is the review {as asked}:\n\n" + json.dumps(SECTIONS) + "\n\nLet me know if you want changes.",
    "<think>{\"sections\": []}</think>" + json.dumps(SECTIONS),
])
def test_parse_sections_is_tolerant(text):
    assert rp.parse_sections(text) == EXPECTED


def test_parse_sections_fills_missing_order_and_rejects_junk():
    assert rp.parse_sections('{"sections":[{"heading":"a","text":"b"}]}') == [{"order": 1, "heading": "a", "text": "b"}]
    for bad in ("no json here", '{"sections": []}', '{"sections":[{"heading":"a"}]}', '{"other": 1}'):
        with pytest.raises(ValueError):
            rp.parse_sections(bad)


def test_non_anthropic_providers_have_no_default_model(cfg):
    cfg(review_provider="ollama", review_base_url="http://o:11434")
    assert rp.resolve().problem() == "REVIEW_MODEL is not set"
    cfg(review_provider="ollama", review_base_url="http://o:11434", review_model_explicit="llama-x")
    assert rp.resolve().problem() is None and rp.resolve().label == "ollama:llama-x"
    cfg(review_provider="gemini", review_model_explicit="g-x")
    assert rp.resolve().problem() == "REVIEW_API_KEY is not set"
    cfg(review_provider="openai", review_model_explicit="o-x", review_base_url="")
    assert rp.resolve().problem() == "REVIEW_BASE_URL is not set"
    cfg(review_provider="bogus")
    assert "must be one of" in rp.resolve().problem()


def test_saved_choice_overrides_environment(cfg):
    cfg(review_provider="anthropic", anthropic_api_key="k")
    p = rp.resolve(override={"provider": "ollama", "model": "m", "base_url": "http://o:11434/"})
    assert (p.name, p.model, p.base_url, p.problem()) == ("ollama", "m", "http://o:11434", None)


def test_generate_stores_provider_and_model_and_tolerates_fences(client, reset_db, cfg, http, monkeypatch):
    from app import db
    reset_db(SAMPLE)
    client.post("/upload", files=[("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(SAMPLE.glob("*EveryDollar*.csv"))])
    calls, reply = http
    cfg(review_provider="ollama", review_base_url="http://o:11434", review_model_explicit="llama-x")
    reply["v"] = Resp({"message": {"content": "Here you go:\n```json\n" + json.dumps(SECTIONS) + "\n```"}})
    with db.pool.connection() as conn:
        month = _first_month(conn)
        assert month
        res = review.generate(conn, month)
        assert res["sections"] == EXPECTED
        row = conn.execute("SELECT model, sections FROM reviews WHERE month=%s", (month,)).fetchone()
    assert row["model"] == "ollama:llama-x" and row["sections"] == EXPECTED
    sent = calls[0][1]["json"]["messages"][1]["content"]
    assert "transactions" not in json.loads(sent) and "merchant" not in sent


def _first_month(conn):
    r = conn.execute("SELECT rows FROM datasets WHERE name='monthly'").fetchone()
    return r["rows"][-1]["m"] if r and r["rows"] else None


def test_generate_refuses_when_unconfigured(client, reset_db, cfg):
    from app import db
    with db.pool.connection() as conn, pytest.raises(RuntimeError, match="no review provider is configured"):
        review.generate(conn, "2026-01")


def test_generate_button_follows_configured_provider(client, reset_db, cfg):
    assert "disabled" in client.get("/upload").text.split("Generate review")[0][-300:]
    cfg(review_provider="ollama", review_base_url="http://o:11434", review_model_explicit="llama-x")
    assert "disabled" not in client.get("/upload").text.split("Generate review")[0][-300:]


def test_reviews_page_save_and_test(client, reset_db, cfg, http):
    calls, reply = http
    assert 'href="/reviews/provider"' in client.get("/reviews").text   # the list first; provider settings one link away
    page = client.get("/reviews/provider")
    assert page.status_code == 200 and "Not configured" in page.text and 'href="/reviews"' in page.text
    r = client.post("/reviews", data={"provider": "openai", "model": "gpt-x", "base_url": "http://lm:1234/v1/"})
    assert r.status_code == 303
    page = client.get("/reviews/provider").text
    assert "Saved." in page and "openai:gpt-x" in page and 'value="gpt-x"' in page
    reply["v"] = Resp({"choices": [{"message": {"content": "ok"}}]})
    assert "Connected to openai:gpt-x" in client.get(client.post("/reviews/test").headers["location"]).text
    assert calls[-1][0] == "http://lm:1234/v1/chat/completions"
    reply["v"] = Resp({}, 500)
    assert "Test failed" in client.get(client.post("/reviews/test").headers["location"]).text
    assert "unknown provider" in client.get(client.post("/reviews", data={"provider": "x"}).headers["location"]).text
    assert "base URL must start" in client.get(client.post("/reviews", data={"provider": "ollama", "base_url": "ftp://x"}).headers["location"]).text


def test_reviews_page_needs_editor(client, reset_db, monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, auth_disabled=False))
    assert client.get("/reviews").status_code == 307
    assert client.post("/reviews/test").status_code in (303, 401, 403)


CUT = '{"sections":[{"order":1,"heading":"The month","text":"Rosie and Sam spent'


@pytest.mark.parametrize("name, kw, body", [
    ("anthropic", {"api_key": "k"}, {"content": [{"type": "text", "text": CUT}], "stop_reason": "max_tokens"}),
    ("openai", {"base_url": "http://lm/v1"}, {"choices": [{"message": {"content": CUT}, "finish_reason": "length"}]}),
    ("ollama", {"base_url": "http://o:11434"}, {"message": {"content": CUT}, "done_reason": "length"}),
    ("gemini", {"api_key": "gk"}, {"candidates": [{"content": {"parts": [{"text": CUT}]}, "finishReason": "MAX_TOKENS"}]}),
])
def test_a_reply_stopped_at_the_token_limit_says_so(http, name, kw, body):
    """A cut-off reply used to surface as 'did not contain a JSON object', which hides the fix (REVIEW_MAX_TOKENS)."""
    _, reply = http
    reply["v"] = Resp(body)
    with pytest.raises(rp.ReplyCutOff, match="REVIEW_MAX_TOKENS \\(now 3000\\)") as e:
        prov(name, **kw).complete("S", "U")
    assert e.value.text == CUT


def test_a_normal_stop_is_not_a_cut_off(http):
    _, reply = http
    reply["v"] = Resp({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    assert prov("anthropic", api_key="k").complete("S", "U") == "ok"


def test_parse_error_tells_invalid_json_from_no_sections():
    with pytest.raises(ValueError, match="not valid JSON"):
        rp.parse_sections('{"sections":[{"order":1,"heading":"Month","text":"In "EveryDollar\'s view" spend rose."}]}')
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        rp.parse_sections("I cannot write this review.")


def test_failed_generate_logs_an_excerpt_of_the_reply(client, reset_db, cfg, http, caplog):
    from app import db
    reset_db(SAMPLE)
    client.post("/upload", files=[("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(SAMPLE.glob("*EveryDollar*.csv"))])
    _, reply = http
    cfg(review_provider="ollama", review_base_url="http://o:11434", review_model_explicit="llama-x")
    reply["v"] = Resp({"message": {"content": "Sorry, the figures look incomplete."}})
    with db.pool.connection() as conn, caplog.at_level("WARNING", logger="ledger"):
        with pytest.raises(ValueError):
            review.generate(conn, _first_month(conn))
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "from ollama:llama-x failed" in msg and "Sorry, the figures look incomplete." in msg


def test_a_limit_spent_on_thinking_says_no_text_was_written(http):
    """A model that thinks first can use the whole limit and return only a thinking block (stop_reason max_tokens, no text)."""
    _, reply = http
    reply["v"] = Resp({"content": [{"type": "thinking", "thinking": "Rosie's paycheck…"}], "stop_reason": "max_tokens"})
    with pytest.raises(rp.ReplyCutOff, match="before writing any reply text"):
        prov("anthropic", api_key="k", max_tokens=2500).complete("S", "U")


def test_default_limit_and_timeout_leave_room_for_thinking(http):
    from app.config import Settings
    assert Settings().review_max_tokens == 16000 or "REVIEW_MAX_TOKENS" in __import__("os").environ
    calls, reply = http
    reply["v"] = Resp({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
    prov("anthropic", api_key="k", max_tokens=16000).complete("S", "U")
    assert calls[0][1]["timeout"] == 400 and calls[0][1]["json"]["max_tokens"] == 16000
    assert rp.read_timeout(prov("anthropic", max_tokens=1000)) == 120


def test_review_message_says_where_to_read_it_and_warns_on_section_count(client, reset_db, cfg, http):
    reset_db(SAMPLE)
    client.post("/upload", files=[("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(SAMPLE.glob("*EveryDollar*.csv"))])
    _, reply = http
    cfg(review_provider="ollama", review_base_url="http://o:11434", review_model_explicit="llama-x")
    reply["v"] = Resp({"message": {"content": json.dumps({"sections": [{"order": 1, "heading": "The month", "text": "Calm."}]})}})
    from app import db
    with db.pool.connection() as conn:
        months = [r["budget_month"] for r in conn.execute("SELECT DISTINCT budget_month FROM source_files ORDER BY 1").fetchall()]
    page = client.get(client.post("/review/generate", data={"month": months[-2], "confirm": "yes"}).headers["location"]).text
    assert f"Review written for {months[-2]} (1 section)." in page and "Past reviews" in page and "ask for 5 to 7" in page
    page = client.get(client.post("/review/generate", data={"month": months[-1], "confirm": "yes"}).headers["location"]).text
    assert "Last month tab" in page
