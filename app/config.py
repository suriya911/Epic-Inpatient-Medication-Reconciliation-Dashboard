"""Environment-driven configuration.

Every Epic-specific value is overridable via environment variable so the same code
runs locally, in Vercel preview, and in production without edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# Epic's public sandbox. These are the documented R4 sandbox endpoints; verify against
# https://fhir.epic.com if Epic moves them.
DEFAULT_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"
DEFAULT_TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
DEFAULT_AUTHORIZE_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize"

# "Camila Lopez", one of Epic's published sandbox test patients. Confirm the current ID
# in Epic's sandbox documentation before relying on it.
DEFAULT_PATIENT_ID = "erXuFYUfucBZaryVksYEcMg3"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    fhir_base: str
    token_url: str
    authorize_url: str
    backend_client_id: str
    patient_client_id: str
    private_key: str
    jwk_kid: str
    demo_mode: str
    default_patient_id: str
    redirect_uri: str
    grace_minutes: int
    miss_multiplier: float
    http_timeout_seconds: float

    @property
    def use_fixtures(self) -> bool:
        """True when the dashboard should serve committed fixture data."""
        return self.demo_mode.lower() == "fixtures"

    @property
    def backend_auth_configured(self) -> bool:
        """True when enough is set to attempt the backend-services token flow."""
        return bool(self.backend_client_id and self.private_key and self.jwk_kid)

    @property
    def patient_auth_configured(self) -> bool:
        """True when enough is set to attempt the standalone-launch flow."""
        return bool(self.patient_client_id and self.redirect_uri)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _private_key() -> str:
    """Read the signing key, tolerating the escaped-newline form env vars force.

    Vercel (and most dashboards) cannot hold a literal multi-line value, so the PEM is
    stored with '\\n' escapes and unescaped here.
    """
    raw = os.environ.get("EPIC_PRIVATE_KEY", "")
    return raw.replace("\\n", "\n").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings once per process."""
    return Settings(
        fhir_base=_env("EPIC_FHIR_BASE", DEFAULT_FHIR_BASE).rstrip("/"),
        token_url=_env("EPIC_TOKEN_URL", DEFAULT_TOKEN_URL),
        authorize_url=_env("EPIC_AUTHORIZE_URL", DEFAULT_AUTHORIZE_URL),
        backend_client_id=_env("EPIC_BACKEND_CLIENT_ID"),
        patient_client_id=_env("EPIC_PATIENT_CLIENT_ID"),
        private_key=_private_key(),
        jwk_kid=_env("EPIC_JWK_KID", "epic-recon-1"),
        # "auto" tries Epic first and falls back to fixtures; "fixtures" never calls Epic;
        # "live" calls Epic and surfaces failures instead of hiding them.
        demo_mode=_env("DEMO_MODE", "auto"),
        default_patient_id=_env("DEFAULT_PATIENT_ID", DEFAULT_PATIENT_ID),
        redirect_uri=_env("EPIC_REDIRECT_URI", "http://localhost:8000/api/auth/callback"),
        grace_minutes=int(_env("RECON_GRACE_MINUTES", "60")),
        miss_multiplier=float(_env("RECON_MISS_MULTIPLIER", "2.0")),
        http_timeout_seconds=float(_env("EPIC_HTTP_TIMEOUT", "8.0")),
    )


def reset_settings_cache() -> None:
    """Drop the cached settings. Used by tests that patch the environment."""
    get_settings.cache_clear()
