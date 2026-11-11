"""Review providers: one small interface, complete(system, user) -> str, behind REVIEW_PROVIDER.

anthropic (ANTHROPIC_API_KEY), openai (any OpenAI-compatible endpoint: REVIEW_BASE_URL, optional REVIEW_API_KEY), ollama
(REVIEW_BASE_URL, no key) and gemini (REVIEW_API_KEY). The provider, model and base URL can be overridden per household on the
Reviews page (settings row "review"); API keys only ever come from the environment. Only the model name for anthropic has a
default: the other providers require REVIEW_MODEL (or the Reviews page) so nothing is guessed.
"""
from __future__ import annotations
import json
from dataclasses import dataclass

import httpx

from . import db
from .config import settings

PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI-compatible", "ollama": "Ollama (local)", "gemini": "Gemini"}
SETTING = "review"


@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    base_url: str
    api_key: str
    max_tokens: int

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def problem(self) -> str | None:
        """Why this provider cannot be used, or None when it is configured."""
        if self.name not in PROVIDERS:
            return f"REVIEW_PROVIDER must be one of {', '.join(PROVIDERS)}"
        if self.name == "anthropic" and not self.api_key:
            return "ANTHROPIC_API_KEY is not set"
        if self.name == "gemini" and not self.api_key:
            return "REVIEW_API_KEY is not set"
        if self.name in ("openai", "ollama") and not self.base_url:
            return "REVIEW_BASE_URL is not set"
        if not self.model:
            return "REVIEW_MODEL is not set" if self.name != "anthropic" else "no model is set"
        return None

    def complete(self, system: str, user: str) -> str:
        text, cut = _CALLS[self.name](self, system, user)
        if cut:   # the JSON cannot be complete: say so instead of a parse error
            what = ("before writing any reply text (a model that thinks first can spend the whole limit thinking)" if not text.strip()
                    else "before finishing its reply")
            raise ReplyCutOff(f"the model stopped at the output limit ({self.max_tokens} tokens) {what}; "
                              f"raise REVIEW_MAX_TOKENS (now {self.max_tokens}) and generate again", text)
        return text


class ReplyCutOff(RuntimeError):
    """The provider reports the reply stopped at max_tokens. `text` is what arrived, for the log."""
    def __init__(self, message: str, text: str = ""):
        super().__init__(message)
        self.text = text


def resolve(conn=None, override: dict | None = None) -> Provider:
    """Environment first, then the household's saved choice (provider, model, base_url) on top."""
    saved = override if override is not None else ((db.get_setting(conn, SETTING, {}) or {}) if conn is not None else {})
    name = (saved.get("provider") or settings.review_provider or "anthropic").strip().lower()
    env_model = settings.review_model if name == "anthropic" else settings.review_model_explicit
    base = (saved.get("base_url") or settings.review_base_url).strip().rstrip("/")
    key = settings.anthropic_api_key if name == "anthropic" else settings.review_api_key
    return Provider(name, (saved.get("model") or env_model).strip(), base, key, settings.review_max_tokens)


def is_configured(conn) -> bool:
    return resolve(conn).problem() is None


def read_timeout(p: Provider) -> float:
    """Seconds to wait for a non-streamed reply: nothing arrives until the model has finished, so this bounds generation.
    Scaled with the output limit at a slow 40 tokens/s (400 s for the default 16000), never under 120 s."""
    return max(120.0, p.max_tokens / 40)


def _post(p: Provider, url: str, headers: dict, body: dict) -> dict:
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=read_timeout(p))
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"{p.name} returned HTTP {e.response.status_code}: {e.response.text[:200]}") from None
    except httpx.HTTPError as e:
        raise RuntimeError(f"could not reach {p.name}: {type(e).__name__}") from None
    except ValueError:
        raise RuntimeError(f"{p.name} did not return JSON") from None


def _anthropic(p: Provider, system: str, user: str) -> str:
    j = _post(p, "https://api.anthropic.com/v1/messages",
              {"x-api-key": p.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
              {"model": p.model, "max_tokens": p.max_tokens, "system": system, "messages": [{"role": "user", "content": user}]})
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text"), j.get("stop_reason") == "max_tokens"


def _openai(p: Provider, system: str, user: str) -> str:
    headers = {"content-type": "application/json", **({"authorization": f"Bearer {p.api_key}"} if p.api_key else {})}
    j = _post(p, f"{p.base_url}/chat/completions", headers,
              {"model": p.model, "max_tokens": p.max_tokens, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    try:
        return j["choices"][0]["message"]["content"] or "", j["choices"][0].get("finish_reason") == "length"
    except (KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError("openai-compatible response had no choices[0].message.content") from None


def _ollama(p: Provider, system: str, user: str) -> str:
    j = _post(p, f"{p.base_url}/api/chat", {"content-type": "application/json"},
              {"model": p.model, "stream": False, "options": {"num_predict": p.max_tokens},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    try:
        return j["message"]["content"] or "", j.get("done_reason") == "length"
    except (KeyError, TypeError):
        raise RuntimeError("ollama response had no message.content") from None


def _gemini(p: Provider, system: str, user: str) -> str:
    base = p.base_url or "https://generativelanguage.googleapis.com/v1beta"
    j = _post(p, f"{base}/models/{p.model}:generateContent", {"x-goog-api-key": p.api_key, "content-type": "application/json"},
              {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
               "generationConfig": {"maxOutputTokens": p.max_tokens}})
    try:
        c = j["candidates"][0]
        return "".join(x.get("text", "") for x in c["content"]["parts"]), c.get("finishReason") == "MAX_TOKENS"
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("gemini response had no candidates[0].content.parts") from None


_CALLS = {"anthropic": _anthropic, "openai": _openai, "ollama": _ollama, "gemini": _gemini}


def parse_sections(text: str) -> list[dict]:
    """Pull {"sections":[...]} out of a model reply that may have markdown fences or prose around the JSON."""
    dec, i = json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("sections"), list) and obj["sections"]:
            out = []
            for n, s in enumerate(obj["sections"], 1):
                if not isinstance(s, dict) or "heading" not in s or "text" not in s:
                    raise ValueError("a section is missing its heading or text")
                try:
                    order = int(s.get("order", n))
                except (TypeError, ValueError):
                    order = n
                out.append({"order": order, "heading": str(s["heading"]), "text": str(s["text"])})
            return out
        i += 1
    if '"sections"' in text:
        raise ValueError("the model's reply has a \"sections\" object, but it is not valid JSON (often an unescaped double quote inside "
                         "a text, or a reply cut short); the reply is in the server log")
    raise ValueError("the model's reply did not contain a JSON object with a non-empty \"sections\" list; the reply is in the server log")
