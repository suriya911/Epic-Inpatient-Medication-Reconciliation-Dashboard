"""SMART on FHIR authentication against Epic's sandbox.

Two flows, both implemented:

* Backend services (``client_credentials`` + an RS384-signed JWT assertion). No human in
  the loop, so the public demo renders for anyone who opens it. This is the default.
* Standalone launch (``authorization_code`` + PKCE). The flow a real patient-facing SMART
  app uses; kept as an optional "Sign in with Epic" path.

The access token is held server-side only and never reaches the browser.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import uuid
from dataclasses import dataclass

import httpx
import jwt

from app.config import Settings, get_settings

# Epic requires a short-lived assertion; 4 minutes sits inside its documented limit.
ASSERTION_LIFETIME_SECONDS = 240

# Refresh this many seconds before actual expiry so a request never races the clock.
TOKEN_REFRESH_MARGIN_SECONDS = 60


class EpicAuthError(RuntimeError):
    """Raised when a token cannot be obtained. Carries Epic's own error text."""


@dataclass
class AccessToken:
    """A bearer token with the wall-clock time it stops being usable."""

    value: str
    expires_at: float
    scope: str = ""
    patient: str = ""

    @property
    def is_usable(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at - TOKEN_REFRESH_MARGIN_SECONDS


# Process-wide cache. On serverless this survives only for the life of a warm instance,
# which is exactly the window where it saves a round trip.
_cached_token: AccessToken | None = None


def build_client_assertion(settings: Settings) -> str:
    """Build and sign the JWT that proves we hold the registered private key."""
    if not settings.backend_client_id:
        raise EpicAuthError("EPIC_BACKEND_CLIENT_ID is not set.")
    if not settings.private_key:
        raise EpicAuthError("EPIC_PRIVATE_KEY is not set.")

    now = int(time.time())
    claims = {
        "iss": settings.backend_client_id,
        "sub": settings.backend_client_id,
        "aud": settings.token_url,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + ASSERTION_LIFETIME_SECONDS,
    }
    try:
        return jwt.encode(
            claims,
            settings.private_key,
            algorithm="RS384",
            headers={"kid": settings.jwk_kid, "typ": "JWT"},
        )
    except Exception as exc:  # noqa: BLE001 - surface key problems with context
        raise EpicAuthError(f"Could not sign the client assertion: {exc}") from exc


async def fetch_backend_token(settings: Settings | None = None) -> AccessToken:
    """Run the backend-services token exchange."""
    settings = settings or get_settings()
    assertion = build_client_assertion(settings)

    form = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(
            settings.token_url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        # Epic's error body names the actual problem (bad kid, unreachable JWKS, wrong
        # aud). Propagating it verbatim turns a blind retry loop into a two-minute fix.
        raise EpicAuthError(
            f"Epic token request failed ({response.status_code}): {response.text[:500]}"
        )

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise EpicAuthError(f"Epic returned no access_token: {payload}")

    return AccessToken(
        value=token,
        expires_at=time.time() + float(payload.get("expires_in", 3600)),
        scope=payload.get("scope", ""),
    )


async def get_access_token(force_refresh: bool = False) -> AccessToken:
    """Return a usable token, reusing the cached one when it is still valid."""
    global _cached_token

    if not force_refresh and _cached_token and _cached_token.is_usable:
        return _cached_token

    _cached_token = await fetch_backend_token()
    return _cached_token


def clear_token_cache() -> None:
    """Drop the cached token. Used by tests and by the manual refresh endpoint."""
    global _cached_token
    _cached_token = None


# --------------------------------------------------------------------------------------
# Standalone launch (authorization_code + PKCE)
# --------------------------------------------------------------------------------------


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def build_authorize_url(
    settings: Settings,
    code_challenge: str,
    state: str,
    scope: str = "openid fhirUser patient/*.read",
) -> str:
    """Build the URL the browser is redirected to for interactive login."""
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": settings.patient_client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": scope,
        "state": state,
        "aud": settings.fhir_base,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{settings.authorize_url}?{urlencode(params)}"


def new_state() -> str:
    """Opaque anti-CSRF value round-tripped through the authorize request."""
    return secrets.token_urlsafe(24)


async def exchange_authorization_code(
    code: str,
    code_verifier: str,
    settings: Settings | None = None,
) -> AccessToken:
    """Swap an authorization code for an access token."""
    settings = settings or get_settings()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "client_id": settings.patient_client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(
            settings.token_url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise EpicAuthError(
            f"Epic code exchange failed ({response.status_code}): {response.text[:500]}"
        )

    payload = response.json()
    if not payload.get("access_token"):
        raise EpicAuthError(f"Epic returned no access_token: {payload}")

    return AccessToken(
        value=payload["access_token"],
        expires_at=time.time() + float(payload.get("expires_in", 3600)),
        scope=payload.get("scope", ""),
        patient=payload.get("patient", ""),
    )
