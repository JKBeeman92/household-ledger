"""Settings from the environment. Every value has a safe default except the ones that must be secret."""
from __future__ import annotations
import os
from dataclasses import dataclass, field


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
    auth_disabled: bool = _bool("AUTH_DISABLED", False)            # local development only
    # month-close review
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    review_model: str = os.environ.get("REVIEW_MODEL", "claude-sonnet-5-5")
    seed_dir: str = os.environ.get("SEED_DIR", "")                  # household seed files (mapping, goals, rules…) read on first boot
    import_dir: str = os.environ.get("IMPORT_DIR", "")              # exports here are bulk-loaded at every start (new/changed files only)

    def validate(self) -> list[str]:
        problems = []
        if not self.session_secret or len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set (32+ random characters)")
        if not self.auth_disabled and not (self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret):
            problems.append("OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET must be set (or AUTH_DISABLED=true for local dev)")
        return problems


settings = Settings()
