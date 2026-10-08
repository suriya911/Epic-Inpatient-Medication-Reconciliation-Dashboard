"""Tests for the SMART on FHIR auth layer.

Epic's sandbox cannot be reached from CI, so these verify the parts that are ours: the
shape of the signed assertion, PKCE generation, token caching, and that failures carry
Epic's own error text instead of a bare exception.
"""

from __future__ import annotations

import base64
import hashlib
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import epic_auth
from app.config import get_settings, reset_settings_cache
from app.epic_auth import (
    AccessToken,
    EpicAuthError,
    build_authorize_url,
    build_client_assertion,
    clear_token_cache,
    fetch_backend_token,
    generate_pkce_pair,
    get_access_token,
)


@pytest.fixture(scope="module")
def keypair() -> tuple[str, object]:
    """A throwaway RSA keypair, generated per test run. Never a committed key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem, key.public_key()


@pytest.fixture()
def configured(monkeypatch, keypair):
    """Settings with backend-services credentials present."""
    pem, public_key = keypair
    monkeypatch.setenv("EPIC_BACKEND_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EPIC_PATIENT_CLIENT_ID", "test-patient-client-id")
    # Stored escaped, exactly as a dashboard env var must hold a PEM.
    monkeypatch.setenv("EPIC_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setenv("EPIC_JWK_KID", "epic-recon-1")
    reset_settings_cache()
    clear_token_cache()
    yield get_settings(), public_key
    clear_token_cache()


# ---------------------------------------------------------------------------------
# Client assertion
# ---------------------------------------------------------------------------------


def test_escaped_newlines_in_the_env_var_are_restored(configured):
    settings, _ = configured
    assert settings.private_key.startswith("-----BEGIN PRIVATE KEY-----")
    assert "\\n" not in settings.private_key
    assert settings.backend_auth_configured is True


def test_assertion_is_signed_rs384_and_carries_the_kid(configured):
    settings, public_key = configured
    token = build_client_assertion(settings)

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS384"
    assert header["kid"] == "epic-recon-1"

    claims = jwt.decode(token, public_key, algorithms=["RS384"],
                        audience=settings.token_url)
    assert claims["iss"] == "test-client-id"
    assert claims["sub"] == "test-client-id"
    assert claims["aud"] == settings.token_url


def test_assertion_is_short_lived(configured):
    settings, public_key = configured
    claims = jwt.decode(build_client_assertion(settings), public_key,
                        algorithms=["RS384"], audience=settings.token_url)
    lifetime = claims["exp"] - claims["iat"]
    assert 0 < lifetime <= 300, "Epic rejects long-lived client assertions"


def test_each_assertion_has_a_unique_jti(configured):
    settings, public_key = configured
    jtis = {
        jwt.decode(build_client_assertion(settings), public_key, algorithms=["RS384"],
                   audience=settings.token_url)["jti"]
        for _ in range(5)
    }
    assert len(jtis) == 5, "a replayed jti would be rejected by Epic"


def test_missing_credentials_raise_a_named_error(monkeypatch):
    monkeypatch.delenv("EPIC_BACKEND_CLIENT_ID", raising=False)
    monkeypatch.delenv("EPIC_PRIVATE_KEY", raising=False)
    reset_settings_cache()
    with pytest.raises(EpicAuthError, match="EPIC_BACKEND_CLIENT_ID"):
        build_client_assertion(get_settings())


def test_malformed_private_key_is_reported_clearly(monkeypatch):
    monkeypatch.setenv("EPIC_BACKEND_CLIENT_ID", "cid")
    monkeypatch.setenv("EPIC_PRIVATE_KEY", "not-a-pem")
    reset_settings_cache()
    with pytest.raises(EpicAuthError, match="sign the client assertion"):
        build_client_assertion(get_settings())


# ---------------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------------


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_successful_token_exchange_sends_the_expected_form(configured, monkeypatch):
    settings, _ = configured
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs

        captured.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600,
                                         "scope": "system/Patient.read"})

    _patch_client(monkeypatch, handler)
    token = await fetch_backend_token(settings)

    assert token.value == "tok-123"
    assert token.scope == "system/Patient.read"
    assert captured["grant_type"] == "client_credentials"
    assert captured["client_assertion_type"] == (
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    )
    assert captured["client_assertion"].count(".") == 2


@pytest.mark.asyncio
async def test_epic_error_body_is_surfaced_verbatim(configured, monkeypatch):
    settings, _ = configured

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":"invalid_client","error_description":'
                                        '"JWKS could not be retrieved"}')

    _patch_client(monkeypatch, handler)
    with pytest.raises(EpicAuthError, match="JWKS could not be retrieved"):
        await fetch_backend_token(settings)


@pytest.mark.asyncio
async def test_missing_access_token_in_response_is_an_error(configured, monkeypatch):
    settings, _ = configured
    _patch_client(monkeypatch, lambda r: httpx.Response(200, json={"token_type": "Bearer"}))
    with pytest.raises(EpicAuthError, match="no access_token"):
        await fetch_backend_token(settings)


def _patch_client(monkeypatch, handler):
    """Route every httpx.AsyncClient in epic_auth through a mock transport."""
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(epic_auth.httpx, "AsyncClient", factory)


# ---------------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------------


def test_fresh_token_is_usable_and_near_expiry_token_is_not():
    assert AccessToken(value="t", expires_at=time.time() + 3600).is_usable is True
    # Inside the 60s refresh margin: treated as unusable so a request never races expiry.
    assert AccessToken(value="t", expires_at=time.time() + 30).is_usable is False
    assert AccessToken(value="", expires_at=time.time() + 3600).is_usable is False


@pytest.mark.asyncio
async def test_cached_token_is_reused_until_forced(configured, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"access_token": f"tok-{calls['n']}",
                                         "expires_in": 3600})

    _patch_client(monkeypatch, handler)

    first = await get_access_token()
    second = await get_access_token()
    assert first.value == second.value == "tok-1"
    assert calls["n"] == 1, "a valid cached token must not trigger a second exchange"

    third = await get_access_token(force_refresh=True)
    assert third.value == "tok-2"
    assert calls["n"] == 2


# ---------------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------------


def test_pkce_challenge_is_the_s256_hash_of_the_verifier():
    verifier, challenge = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge, "PKCE values must be unpadded base64url"


def test_pkce_pairs_are_unique_per_login():
    assert generate_pkce_pair()[0] != generate_pkce_pair()[0]


def test_authorize_url_carries_every_required_smart_parameter(configured):
    settings, _ = configured
    url = build_authorize_url(settings, "challenge-value", "state-value")
    for required in (
        "response_type=code",
        "client_id=test-patient-client-id",
        "code_challenge=challenge-value",
        "code_challenge_method=S256",
        "state=state-value",
        "aud=",           # Epic requires the FHIR base as the audience
    ):
        assert required in url, f"missing {required} in {url}"
