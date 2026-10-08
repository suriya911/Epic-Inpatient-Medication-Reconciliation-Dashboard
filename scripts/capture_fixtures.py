"""Capture a real Epic sandbox patient into a committed fixture file.

Run once Epic credentials are configured, to replace the synthetic demo data with a
genuine sandbox capture:

    python scripts/capture_fixtures.py --patient erXuFYUfucBZaryVksYEcMg3

Or probe several published test patients and keep the richest one, which is the practical
way to find a sandbox patient that actually has MedicationAdministration records:

    python scripts/capture_fixtures.py --probe

Captured files are Epic sandbox test data, not PHI, and are safe to commit. The written
fixture records provenance as EPIC_SANDBOX so the dashboard banner stops calling it
synthetic demo data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.epic_auth import EpicAuthError, get_access_token  # noqa: E402
from app.fhir_client import EpicFhirClient, FhirError  # noqa: E402

FIXTURE_DIR = ROOT / "app" / "fixtures"

# Epic's published sandbox test patients. Verify the current list and IDs at
# https://fhir.epic.com — Epic refreshes sandbox data periodically.
KNOWN_TEST_PATIENTS: dict[str, str] = {
    "Camila Lopez": "erXuFYUfucBZaryVksYEcMg3",
    "Derrick Lin": "eq081-VQEgP8drUUqCWzHfw3",
    "Desiree Powell": "eAB3mDIBBcyUKviyzrxsnAw3",
    "Elijah Davies": "eh2xYHuzl9nkSFVvV3osUHg3",
    "Linda Ross": "eAB3mDIBBcyUKviyzrxsnAw3",
}


async def capture_one(patient_id: str) -> dict:
    """Fetch every resource the dashboard uses, as raw FHIR."""
    token = await get_access_token()
    client = EpicFhirClient(token.value)

    import httpx

    settings = get_settings()
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as http:
        patient = await client._get(http, f"Patient/{patient_id}")
        encounters = await client._search(http, "Encounter",
                                          {"patient": patient_id, "class": "IMP"})
        orders = await client._search(http, "MedicationRequest",
                                      {"patient": patient_id, "_count": 50})
        observations = await client._search(http, "Observation",
                                            {"patient": patient_id,
                                             "category": "vital-signs", "_count": 20})

        administrations: list[dict] = []
        for order in orders:
            order_id = order.get("id")
            if not order_id:
                continue
            try:
                administrations.extend(await client._search(
                    http, "MedicationAdministration",
                    {"patient": patient_id, "request": order_id},
                ))
            except FhirError as exc:
                print(f"  ! administration search failed for {order_id}: {exc}")

    return {
        "_meta": {
            "provenance": "EPIC_SANDBOX",
            "note": ("Captured from Epic's public FHIR R4 sandbox. Sandbox test data, "
                     "not PHI."),
            "generated": date.today().isoformat(),
            "patientId": patient_id,
        },
        "patient": patient,
        "encounters": encounters,
        "medicationRequests": orders,
        "medicationAdministrations": administrations,
        "observations": observations,
    }


def describe(capture: dict) -> str:
    return (f"{len(capture['encounters'])} inpatient encounters, "
            f"{len(capture['medicationRequests'])} orders, "
            f"{len(capture['medicationAdministrations'])} administrations")


async def probe() -> None:
    """Report how much medication data each known test patient actually has."""
    print("Probing Epic's published sandbox test patients:\n")
    results: list[tuple[int, str, str]] = []

    for name, patient_id in KNOWN_TEST_PATIENTS.items():
        try:
            capture = await capture_one(patient_id)
        except (EpicAuthError, FhirError) as exc:
            print(f"  {name:16} FAILED  {exc}")
            continue
        admin_count = len(capture["medicationAdministrations"])
        results.append((admin_count, name, patient_id))
        print(f"  {name:16} {describe(capture)}")

    if not results:
        print("\nNo patient could be read. Check credentials with /api/token-check.")
        return

    results.sort(reverse=True)
    best_count, best_name, best_id = results[0]
    print(f"\nRichest: {best_name} ({best_id}) with {best_count} administrations.")
    if best_count == 0:
        print("No test patient returned administration records. Keep the synthetic "
              "fixtures and say so plainly in the README.")
    else:
        print(f"Capture it with:\n  python scripts/capture_fixtures.py --patient {best_id}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient", help="Epic sandbox Patient logical id")
    parser.add_argument("--probe", action="store_true",
                        help="Check every known test patient and report which has data")
    parser.add_argument("--name", help="Fixture filename stem (default: the patient id)")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.backend_auth_configured:
        print("Epic credentials are not configured. Set EPIC_BACKEND_CLIENT_ID and "
              "EPIC_PRIVATE_KEY first (see .env.example).")
        return 1

    if args.probe:
        await probe()
        return 0

    if not args.patient:
        parser.error("supply --patient <id>, or --probe to survey the test patients")

    try:
        capture = await capture_one(args.patient)
    except (EpicAuthError, FhirError) as exc:
        print(f"Capture failed: {exc}")
        return 1

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURE_DIR / f"{args.name or args.patient}.json"
    out.write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {out}")
    print(f"  {describe(capture)}")
    if not capture["medicationAdministrations"]:
        print("  ! No administration records: every order will reconcile as missing. "
              "Try --probe to find a richer patient.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
