"""Settings from the environment. Every value has a safe default except the ones that must be secret."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


class ConfigError(RuntimeError):
    pass


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("DATABASE_URL", "postgresql://ledger:ledger@localhost:5432/ledger")
    base_url: str = os.environ.get("BASE_URL", "http://localhost:8010").rstrip("/")
    port: int = int(os.environ.get("PORT", "8010"))
    session_secret: str = os.environ.get("SESSION_SECRET", "")
    # OIDC against Authentik (or any provider with a discovery document)
    oidc_issuer: str = os.environ.get("OIDC_ISSUER", "")           # e.g. https://auth.example.com/application/o/ledger/
    oidc_client_id: str = os.environ.get("OIDC_CLIENT_ID", "")
    oidc_client_secret: str = os.environ.get("OIDC_CLIENT_SECRET", "")
    allowed_groups: tuple[str, ...] = tuple(g.strip() for g in os.environ.get("ALLOWED_GROUPS", "admins").split(",") if g.strip())
    editor_groups: tuple[str, ...] = tuple(g.strip() for g in os.environ.get("EDITOR_GROUPS", "").split(",") if g.strip())   # empty = every allowed group edits
    auth_disabled: bool = _bool("AUTH_DISABLED", False)            # local development only
    # month-close review
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    review_model: str = os.environ.get("REVIEW_MODEL", "claude-sonnet-5-5")
    review_provider: str = os.environ.get("REVIEW_PROVIDER", "anthropic")   # anthropic | openai | ollama | gemini
    review_model_explicit: str = os.environ.get("REVIEW_MODEL", "")         # the non-Anthropic providers have no default model
    review_base_url: str = os.environ.get("REVIEW_BASE_URL", "")
    review_api_key: str = os.environ.get("REVIEW_API_KEY", "")
    review_max_tokens: int = int(os.environ.get("REVIEW_MAX_TOKENS", "2500"))
    seed_dir: str = os.environ.get("SEED_DIR", "")                  # household seed files (mapping, goals, rules…) read on first boot
    import_dir: str = os.environ.get("IMPORT_DIR", "")              # exports here are bulk-loaded at every start (new/changed files only)
    i_know_auth_is_off: bool = _bool("I_KNOW_AUTH_IS_OFF", False)   # allows AUTH_DISABLED=true on a non-localhost BASE_URL
    session_max_age: int = int(os.environ.get("SESSION_MAX_AGE", str(12 * 3600)))       # seconds from sign-in; never extended
    group_recheck_seconds: int = int(os.environ.get("GROUP_RECHECK_SECONDS", "300"))    # how soon a removed group member is locked out
    # extra Content-Security-Policy sources (space-separated) for a BRAND_DIR theme; defaults cover the shipped themes' Google Fonts
    csp_style_src: str = os.environ.get("CSP_STYLE_SRC", "https://fonts.googleapis.com")
    csp_font_src: str = os.environ.get("CSP_FONT_SRC", "https://fonts.gstatic.com")
    csp_img_src: str = os.environ.get("CSP_IMG_SRC", "")

    @property
    def editors(self) -> tuple[str, ...]:
        """Groups whose members may change things; EDITOR_GROUPS, or ALLOWED_GROUPS when it is unset."""
        return self.editor_groups or self.allowed_groups

    def validate(self) -> list[str]:
        problems = []
        for name in ("csp_style_src", "csp_font_src", "csp_img_src"):
            if any(c in getattr(self, name) for c in ";,\r\n"):
                problems.append(f"{name.upper()} must be space-separated sources (no ';' or ',')")
        if self.auth_disabled:
            host = urlparse(self.base_url).hostname or ""
            if host not in {"localhost", "127.0.0.1", "::1"} and not self.i_know_auth_is_off:
                problems.append(f"AUTH_DISABLED=true with BASE_URL host '{host}' would expose the ledger to anyone who can reach it; use localhost or set I_KNOW_AUTH_IS_OFF=1")
            return problems
        if len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set (32+ random characters)")
        for name, value in (("OIDC_ISSUER", self.oidc_issuer), ("OIDC_CLIENT_ID", self.oidc_client_id), ("OIDC_CLIENT_SECRET", self.oidc_client_secret)):
            if not value:
                problems.append(f"{name} must be set (or AUTH_DISABLED=true for local dev)")
        if not 0 < self.group_recheck_seconds <= self.session_max_age:
            problems.append("GROUP_RECHECK_SECONDS must be positive and no longer than SESSION_MAX_AGE")
        if not self.allowed_groups:
            problems.append("ALLOWED_GROUPS must list at least one group: empty would admit every account the identity provider authenticates")
        return problems

    def enforce(self) -> None:
        """Refuse to start on any validation problem, printing the whole list."""
        problems = self.validate()
        if problems:
            raise ConfigError("invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))


settings = Settings()
