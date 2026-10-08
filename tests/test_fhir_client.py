"""Tests for the Epic FHIR read layer, against mocked sandbox responses.

Covers the behaviours that only show up against a real server: pagination, the
per-order MedicationAdministration fan-out, partial failure, and the DEMO_MODE fallback
that keeps the dashboard populated when Epic is unreachable.
"""

from __future__ import annotations

import httpx
import pytest

from app import fhir_client
from app.config import get_settings, reset_settings_cache
from app.epic_auth import AccessToken, EpicAuthError
from app.fhir_client import EpicFhirClient, FhirError, bundle_entries, load_bundle, next_link


def searchset(resources: list[dict], next_url: str | None = None) -> dict:
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }
    if next_url:
        bundle["link"] = [{"relation": "next", "url": next_url}]
    return bundle


def make_client(monkeypatch, handler) -> EpicFhirClient:
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(fhir_client.httpx, "AsyncClient", factory)
    return EpicFhirClient(token="test-token")


# ---------------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------------


def test_bundle_entries_ignores_malformed_entries():
    bundle = {"entry": [{"resource": {"id": "a"}}, {"search": {"mode": "match"}},
                        {"resource": "not-a-dict"}]}
    assert bundle_entries(bundle) == [{"id": "a"}]


def test_bundle_entries_handles_an_empty_result():
    assert bundle_entries({"resourceType": "Bundle", "total": 0}) == []


def test_next_link_found_only_for_the_next_relation():
    assert next_link({"link": [{"relation": "self", "url": "u1"},
                               {"relation": "next", "url": "u2"}]}) == "u2"
    assert next_link({"link": [{"relation": "self", "url": "u1"}]}) is None


# ---------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_token_is_sent_on_every_request(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        if "Patient/" in str(request.url):
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        return httpx.Response(200, json=searchset([]))

    client = make_client(monkeypatch, handler)
    await client.fetch_patient_bundle("p1")
    assert seen and all(h == "Bearer test-token" for h in seen)


@pytest.mark.asyncio
async def test_pagination_is_followed(monkeypatch):
    page_two = "https://fhir.epic.com/page2"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(page_two):
            return httpx.Response(200, json=searchset(
                [{"resourceType": "MedicationRequest", "id": "mr-2", "status": "active"}]))
        if "MedicationRequest" in url:
            return httpx.Response(200, json=searchset(
                [{"resourceType": "MedicationRequest", "id": "mr-1", "status": "active"}],
                next_url=page_two))
        if "Patient/" in url:
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        return httpx.Response(200, json=searchset([]))

    client = make_client(monkeypatch, handler)
    bundle = await client.fetch_patient_bundle("p1")
    assert {o.id for o in bundle.orders} == {"mr-1", "mr-2"}


@pytest.mark.asyncio
async def test_administrations_are_fetched_per_order(monkeypatch):
    """Epic constrains MedicationAdministration search, so we fan out by request id."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "MedicationAdministration" in url:
            request_id = request.url.params.get("request", "")
            requested.append(request_id)
            return httpx.Response(200, json=searchset([{
                "resourceType": "MedicationAdministration",
                "id": f"ma-{request_id}",
                "status": "completed",
                "effectiveDateTime": "2026-09-18T08:05:00Z",
                "request": {"reference": f"MedicationRequest/{request_id}"},
            }]))
        if "MedicationRequest" in url:
            return httpx.Response(200, json=searchset([
                {"resourceType": "MedicationRequest", "id": "mr-1", "status": "active"},
                {"resourceType": "MedicationRequest", "id": "mr-2", "status": "active"},
            ]))
        if "Patient/" in url:
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        return httpx.Response(200, json=searchset([]))

    client = make_client(monkeypatch, handler)
    bundle = await client.fetch_patient_bundle("p1")
    assert sorted(requested) == ["mr-1", "mr-2"]
    assert {a.id for a in bundle.administrations} == {"ma-mr-1", "ma-mr-2"}


@pytest.mark.asyncio
async def test_one_failing_administration_search_does_not_blank_the_chart(monkeypatch):
    """A single bad fan-out call must degrade that order, not the whole dashboard."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "MedicationAdministration" in url:
            if request.url.params.get("request") == "mr-1":
                return httpx.Response(500, text="Epic internal error")
            return httpx.Response(200, json=searchset([{
                "resourceType": "MedicationAdministration", "id": "ma-ok",
                "status": "completed", "effectiveDateTime": "2026-09-18T08:05:00Z",
                "request": {"reference": "MedicationRequest/mr-2"},
            }]))
        if "MedicationRequest" in url:
            return httpx.Response(200, json=searchset([
                {"resourceType": "MedicationRequest", "id": "mr-1", "status": "active"},
                {"resourceType": "MedicationRequest", "id": "mr-2", "status": "active"},
            ]))
        if "Patient/" in url:
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        return httpx.Response(200, json=searchset([]))

    client = make_client(monkeypatch, handler)
    bundle = await client.fetch_patient_bundle("p1")
    assert {a.id for a in bundle.administrations} == {"ma-ok"}
    assert len(bundle.orders) == 2


@pytest.mark.asyncio
async def test_duplicate_administrations_are_deduplicated(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "MedicationAdministration" in url:
            return httpx.Response(200, json=searchset([{
                "resourceType": "MedicationAdministration", "id": "ma-dup",
                "status": "completed", "effectiveDateTime": "2026-09-18T08:05:00Z",
            }]))
        if "MedicationRequest" in url:
            return httpx.Response(200, json=searchset([
                {"resourceType": "MedicationRequest", "id": "mr-1", "status": "active"},
                {"resourceType": "MedicationRequest", "id": "mr-2", "status": "active"},
            ]))
        if "Patient/" in url:
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        return httpx.Response(200, json=searchset([]))

    client = make_client(monkeypatch, handler)
    bundle = await client.fetch_patient_bundle("p1")
    assert len(bundle.administrations) == 1


@pytest.mark.asyncio
async def test_404_on_a_read_is_treated_as_absent_not_fatal(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "Patient/" in str(request.url):
            return httpx.Response(404, json={"resourceType": "OperationOutcome"})
        return httpx.Response(200, json=searchset([]))

    client = make_client(monkeypatch, handler)
    bundle = await client.fetch_patient_bundle("missing")
    assert bundle.patient.id == "missing"
    assert bundle.patient.name == "Unknown patient"


@pytest.mark.asyncio
async def test_server_errors_raise_with_epic_context(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="insufficient scope for Patient.Read")

    client = make_client(monkeypatch, handler)
    with pytest.raises(FhirError, match="insufficient scope"):
        await client.fetch_patient_bundle("p1")


# ---------------------------------------------------------------------------------
# DEMO_MODE behaviour
# ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixtures_mode_never_calls_epic(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "fixtures")
    reset_settings_cache()

    def explode(*args, **kwargs):
        raise AssertionError("fixtures mode must not open a network client")

    monkeypatch.setattr(fhir_client.httpx, "AsyncClient", explode)
    bundle = await load_bundle("demo-patient-001")
    assert bundle.source == "fixtures"


@pytest.mark.asyncio
async def test_auto_mode_falls_back_to_fixtures_when_epic_fails(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "auto")
    monkeypatch.setenv("EPIC_BACKEND_CLIENT_ID", "cid")
    monkeypatch.setenv("EPIC_PRIVATE_KEY", "key")
    reset_settings_cache()

    async def failing_token(*args, **kwargs):
        raise EpicAuthError("JWKS could not be retrieved")

    monkeypatch.setattr(fhir_client, "get_access_token", failing_token)
    bundle = await load_bundle("demo-patient-001")

    assert bundle.source == "fixtures"
    # The banner must say why, so nobody mistakes demo data for live sandbox data.
    assert "Epic sandbox unavailable" in bundle.source_note
    assert "JWKS" in bundle.source_note


@pytest.mark.asyncio
async def test_live_mode_surfaces_the_failure_instead_of_hiding_it(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "live")
    monkeypatch.setenv("EPIC_BACKEND_CLIENT_ID", "cid")
    monkeypatch.setenv("EPIC_PRIVATE_KEY", "key")
    reset_settings_cache()

    async def failing_token(*args, **kwargs):
        raise EpicAuthError("invalid_client")

    monkeypatch.setattr(fhir_client, "get_access_token", failing_token)
    with pytest.raises(EpicAuthError, match="invalid_client"):
        await load_bundle("demo-patient-001")


@pytest.mark.asyncio
async def test_auto_mode_without_credentials_explains_what_is_missing(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "auto")
    monkeypatch.delenv("EPIC_BACKEND_CLIENT_ID", raising=False)
    monkeypatch.delenv("EPIC_PRIVATE_KEY", raising=False)
    reset_settings_cache()

    bundle = await load_bundle("demo-patient-001")
    assert bundle.source == "fixtures"
    assert "EPIC_BACKEND_CLIENT_ID" in bundle.source_note


@pytest.mark.asyncio
async def test_live_mode_without_credentials_refuses_rather_than_pretending(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "live")
    monkeypatch.delenv("EPIC_BACKEND_CLIENT_ID", raising=False)
    monkeypatch.delenv("EPIC_PRIVATE_KEY", raising=False)
    reset_settings_cache()

    with pytest.raises(EpicAuthError, match="not configured"):
        await load_bundle("demo-patient-001")


@pytest.mark.asyncio
async def test_live_read_is_used_when_epic_succeeds(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "auto")
    monkeypatch.setenv("EPIC_BACKEND_CLIENT_ID", "cid")
    monkeypatch.setenv("EPIC_PRIVATE_KEY", "key")
    reset_settings_cache()

    async def fake_token(*args, **kwargs):
        return AccessToken(value="tok", expires_at=9e12)

    def handler(request: httpx.Request) -> httpx.Response:
        if "Patient/" in str(request.url):
            return httpx.Response(200, json={
                "resourceType": "Patient", "id": "p-live",
                "name": [{"text": "Sandbox Patient"}],
            })
        return httpx.Response(200, json=searchset([]))

    monkeypatch.setattr(fhir_client, "get_access_token", fake_token)
    make_client(monkeypatch, handler)

    bundle = await load_bundle("p-live")
    assert bundle.source == "live"
    assert bundle.patient.name == "Sandbox Patient"
    assert "Live read" in bundle.source_note
