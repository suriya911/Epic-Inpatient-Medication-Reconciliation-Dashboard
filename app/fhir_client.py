"""Read clinical data from Epic's FHIR R4 sandbox.

Every call is scoped to one patient. Administrations are fetched per MedicationRequest
because Epic constrains MedicationAdministration search to a patient plus a request
reference; the fan-out runs concurrently so the extra calls cost latency once, not N
times.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.epic_auth import EpicAuthError, get_access_token
from app.models import (
    Administration,
    Encounter,
    Order,
    Patient,
    PatientBundle,
    VitalSign,
)

log = logging.getLogger(__name__)

FHIR_HEADERS = {"Accept": "application/fhir+json"}

# Cap pagination so a wide search cannot hang a serverless invocation.
MAX_PAGES = 5


class FhirError(RuntimeError):
    """A FHIR read failed. Message carries Epic's status and body."""


def bundle_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the resources out of a FHIR searchset Bundle."""
    entries = bundle.get("entry") or []
    resources = []
    for entry in entries:
        resource = entry.get("resource")
        if isinstance(resource, dict):
            resources.append(resource)
    return resources


def next_link(bundle: dict[str, Any]) -> str | None:
    """Find the 'next' page URL in a Bundle, if there is one."""
    for link in bundle.get("link") or []:
        if link.get("relation") == "next" and link.get("url"):
            return str(link["url"])
    return None


class EpicFhirClient:
    """Thin async client over the Epic sandbox REST API."""

    def __init__(self, token: str, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.token = token
        self._headers = {**FHIR_HEADERS, "Authorization": f"Bearer {token}"}

    async def _get(
        self,
        client: httpx.AsyncClient,
        path_or_url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = (path_or_url if path_or_url.startswith("http")
               else f"{self.settings.fhir_base}/{path_or_url.lstrip('/')}")
        response = await client.get(url, params=params, headers=self._headers)
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise FhirError(
                f"FHIR GET {url} failed ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    async def _search(
        self,
        client: httpx.AsyncClient,
        resource: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run a search and follow 'next' links up to MAX_PAGES."""
        collected: list[dict[str, Any]] = []
        bundle = await self._get(client, resource, params)
        pages = 0
        while bundle and pages < MAX_PAGES:
            collected.extend(bundle_entries(bundle))
            following = next_link(bundle)
            if not following:
                break
            bundle = await self._get(client, following)
            pages += 1
        return collected

    async def fetch_patient_bundle(self, patient_id: str) -> PatientBundle:
        """Fetch everything the dashboard needs for one patient."""
        timeout = httpx.Timeout(self.settings.http_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            patient_raw, encounters_raw, orders_raw, vitals_raw = await asyncio.gather(
                self._get(client, f"Patient/{patient_id}"),
                self._search(client, "Encounter",
                             {"patient": patient_id, "class": "IMP"}),
                self._search(client, "MedicationRequest",
                             {"patient": patient_id, "_count": 50}),
                self._search(client, "Observation",
                             {"patient": patient_id, "category": "vital-signs",
                              "_count": 20}),
            )

            orders = [Order.from_fhir(r) for r in orders_raw]

            # Epic requires patient + request for MedicationAdministration search, so
            # fan out one call per order and gather them concurrently.
            admin_results = await asyncio.gather(
                *[
                    self._search(client, "MedicationAdministration",
                                 {"patient": patient_id, "request": order.id})
                    for order in orders if order.id
                ],
                return_exceptions=True,
            )

        administrations: list[Administration] = []
        seen: set[str] = set()
        for result in admin_results:
            if isinstance(result, BaseException):
                # One order's administrations failing must not blank the whole chart.
                log.warning("MedicationAdministration search failed: %s", result)
                continue
            for raw in result:
                admin = Administration.from_fhir(raw)
                if admin.id and admin.id in seen:
                    continue
                if admin.id:
                    seen.add(admin.id)
                administrations.append(admin)

        patient = (Patient.from_fhir(patient_raw) if patient_raw
                   else Patient(id=patient_id, name="Unknown patient"))

        return PatientBundle(
            patient=patient,
            encounters=[Encounter.from_fhir(r) for r in encounters_raw],
            orders=orders,
            administrations=administrations,
            vitals=[VitalSign.from_fhir(r) for r in vitals_raw],
            source="live",
            source_note="Live read from Epic's public FHIR R4 sandbox.",
        )


async def load_live_bundle(patient_id: str) -> PatientBundle:
    """Authenticate and fetch a patient bundle from Epic."""
    token = await get_access_token()
    client = EpicFhirClient(token.value)
    return await client.fetch_patient_bundle(patient_id)


async def load_bundle(patient_id: str, settings: Settings | None = None) -> PatientBundle:
    """Load a patient bundle honouring DEMO_MODE.

    * ``fixtures`` never calls Epic.
    * ``live`` calls Epic and lets failures surface.
    * ``auto`` (default) tries Epic and falls back to fixtures, so the public demo shows
      a working dashboard with a visible provenance banner instead of an error page.
    """
    from app.fixtures_loader import load_fixture_bundle

    settings = settings or get_settings()
    mode = settings.demo_mode.lower()

    if mode == "fixtures":
        return load_fixture_bundle(patient_id)

    if not settings.backend_auth_configured:
        if mode == "live":
            raise EpicAuthError(
                "Live mode requested but EPIC_BACKEND_CLIENT_ID / EPIC_PRIVATE_KEY are "
                "not configured. Set them, or use DEMO_MODE=fixtures."
            )
        bundle = load_fixture_bundle(patient_id)
        bundle.source_note = (
            "Epic credentials are not configured, so demo data is shown. "
            "Set EPIC_BACKEND_CLIENT_ID and EPIC_PRIVATE_KEY for live sandbox data."
        )
        return bundle

    try:
        return await load_live_bundle(patient_id)
    except (EpicAuthError, FhirError, httpx.HTTPError) as exc:
        if mode == "live":
            raise
        log.warning("Falling back to fixtures: %s", exc)
        bundle = load_fixture_bundle(patient_id)
        bundle.source_note = (
            f"Epic sandbox unavailable, showing demo data. Reason: {exc}"[:400]
        )
        return bundle
