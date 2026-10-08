"""Load committed FHIR fixtures through the same parsing path as live Epic data.

Fixtures exist for two reasons: the sandbox is a third-party dependency that can be slow
or unavailable mid-demo, and Epic's sandbox patients carry few MedicationAdministration
records. Either way the dashboard must show something honest rather than an empty screen.

The fixtures shipped in this repo are SYNTHETIC — hand-authored FHIR R4 resources shaped
like Epic sandbox responses, containing no real patient data. Run
``scripts/capture_fixtures.py`` once you hold an Epic client ID to replace them with a
real capture.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models import (
    Administration,
    Encounter,
    Order,
    Patient,
    PatientBundle,
    VitalSign,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class FixtureNotFound(LookupError):
    """No fixture file exists for the requested patient."""


@lru_cache(maxsize=16)
def _read_fixture(path_str: str) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def available_fixture_ids() -> list[str]:
    """Patient IDs with a committed fixture, in stable order."""
    if not FIXTURE_DIR.exists():
        return []
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))


def default_fixture_id() -> str:
    """The patient shown when the caller names none."""
    ids = available_fixture_ids()
    if not ids:
        raise FixtureNotFound("No fixture files are present in app/fixtures/.")
    return ids[0]


def _resolve_path(patient_id: str) -> Path:
    """Map a patient ID to its fixture file, falling back to the default patient.

    A live Epic patient ID has no fixture of its own, so requesting one while Epic is
    unreachable resolves to the default demo patient rather than erroring.
    """
    candidate = FIXTURE_DIR / f"{patient_id}.json"
    if candidate.exists():
        return candidate
    return FIXTURE_DIR / f"{default_fixture_id()}.json"


def load_fixture_bundle(patient_id: str | None = None) -> PatientBundle:
    """Build a PatientBundle from a committed fixture file."""
    if not FIXTURE_DIR.exists() or not available_fixture_ids():
        raise FixtureNotFound("No fixture files are present in app/fixtures/.")

    path = _resolve_path(patient_id or default_fixture_id())
    raw = _read_fixture(str(path))
    meta = raw.get("_meta") or {}

    provenance = str(meta.get("provenance", "SYNTHETIC")).upper()
    if provenance == "SYNTHETIC":
        note = ("Synthetic demo data (FHIR R4 shaped). Not captured from Epic and not "
                "real patient data.")
    else:
        note = (f"Fixture captured from Epic's public sandbox on "
                f"{meta.get('generated', 'an unrecorded date')}.")

    return PatientBundle(
        patient=Patient.from_fhir(raw.get("patient") or {}),
        encounters=[Encounter.from_fhir(r) for r in raw.get("encounters") or []],
        orders=[Order.from_fhir(r) for r in raw.get("medicationRequests") or []],
        administrations=[
            Administration.from_fhir(r) for r in raw.get("medicationAdministrations") or []
        ],
        vitals=[VitalSign.from_fhir(r) for r in raw.get("observations") or []],
        source="fixtures",
        source_note=note,
    )


def fixture_patients() -> list[dict[str, str]]:
    """Summary of every fixture patient, for the patient picker."""
    patients = []
    for fixture_id in available_fixture_ids():
        raw = _read_fixture(str(FIXTURE_DIR / f"{fixture_id}.json"))
        patient = Patient.from_fhir(raw.get("patient") or {})
        patients.append({
            "id": patient.id or fixture_id,
            "name": patient.name,
            "birthDate": patient.birth_date,
            "gender": patient.gender,
        })
    return patients
