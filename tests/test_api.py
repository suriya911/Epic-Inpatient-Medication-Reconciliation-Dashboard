"""End-to-end API tests against the FastAPI app.

DEMO_MODE=fixtures (set in conftest) keeps every test offline, so these run in CI with
no Epic credentials and no network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.index import app
from app.fixtures_loader import available_fixture_ids, load_fixture_bundle
from app.reconcile import reconcile_bundle


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_configuration(client: TestClient):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["demoMode"] == "fixtures"
    assert "fhir.epic.com" in body["fhirBase"]


def test_api_root_lists_endpoints(client: TestClient):
    body = client.get("/api").json()
    assert "/api/reconciliation?patient=<id>" in body["endpoints"]


def test_patients_endpoint_lists_the_fixture_patients(client: TestClient):
    body = client.get("/api/patients").json()
    ids = {p["id"] for p in body["patients"]}
    assert {"demo-patient-001", "demo-patient-002"} <= ids
    assert body["defaultPatientId"]


def test_reconciliation_returns_every_status_for_the_demo_patient(client: TestClient):
    body = client.get("/api/reconciliation", params={"patient": "demo-patient-001"}).json()

    statuses = {row["status"] for row in body["rows"]}
    # The demo fixture is built to exercise the whole engine, so a regression that
    # collapses statuses into one bucket fails here.
    assert {"ON_TIME", "OVERDUE", "MISSED", "PRN", "INACTIVE", "UNSCHEDULED"} == statuses
    assert body["summary"]["TOTAL"] == len(body["rows"])
    assert body["dataSource"] == "fixtures"
    assert "Synthetic" in body["dataSourceNote"]


def test_as_of_is_historical_not_wall_clock(client: TestClient):
    body = client.get("/api/reconciliation", params={"patient": "demo-patient-001"}).json()
    assert body["asOf"].startswith("2026-09-19")
    assert body["asOfSource"] == "latest-clinical-timestamp"


def test_rows_are_sorted_worst_first(client: TestClient):
    body = client.get("/api/reconciliation", params={"patient": "demo-patient-001"}).json()
    severities = [row["severity"] for row in body["rows"]]
    assert severities == sorted(severities)
    assert body["rows"][0]["status"] == "MISSED"


def test_encounter_filter_narrows_rows(client: TestClient):
    body = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "encounter": "demo-enc-001",
    }).json()
    assert body["rows"]
    assert {row["encounterId"] for row in body["rows"]} == {"demo-enc-001"}


def test_status_filter_narrows_rows(client: TestClient):
    body = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "status": "MISSED,OVERDUE",
    }).json()
    assert {row["status"] for row in body["rows"]} <= {"MISSED", "OVERDUE"}
    assert body["rows"]


def test_unknown_status_filter_is_rejected(client: TestClient):
    response = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "status": "BANANA",
    })
    assert response.status_code == 400
    assert "BANANA" in response.json()["detail"]


def test_as_of_override_changes_the_picture(client: TestClient):
    """Time-travelling to admission should leave nothing missed yet."""
    body = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "as_of": "2026-09-18T08:30:00Z",
    }).json()
    assert body["asOfSource"] == "override"
    assert body["summary"]["MISSED"] == 0


def test_unparseable_as_of_is_rejected(client: TestClient):
    response = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "as_of": "yesterday-ish",
    })
    assert response.status_code == 400


def test_grace_window_is_tunable(client: TestClient):
    """A zero-minute grace turns a 10-minute-late dose into a flagged one."""
    strict = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "grace_minutes": 0,
    }).json()
    lenient = client.get("/api/reconciliation", params={
        "patient": "demo-patient-001", "grace_minutes": 60,
    }).json()
    assert strict["summary"]["ON_TIME"] < lenient["summary"]["ON_TIME"]


def test_discharged_patient_uses_discharge_time_as_as_of(client: TestClient):
    body = client.get("/api/reconciliation", params={"patient": "demo-patient-002"}).json()
    assert body["asOf"].startswith("2026-09-17T16:00")
    assert body["summary"]["TOTAL"] > 0


def test_unknown_patient_falls_back_to_the_demo_patient(client: TestClient):
    """A live Epic patient ID has no fixture; fixture mode must still render."""
    body = client.get("/api/reconciliation", params={"patient": "erXuFYUfucBZ"}).json()
    assert body["rows"]
    assert body["dataSource"] == "fixtures"


def test_encounters_and_vitals_are_returned_for_context(client: TestClient):
    body = client.get("/api/reconciliation", params={"patient": "demo-patient-001"}).json()
    assert any(enc["classCode"] == "IMP" for enc in body["encounters"])
    assert any("Heart rate" in v["label"] for v in body["vitals"])


def test_debug_as_of_explains_the_choice(client: TestClient):
    body = client.get("/api/debug/as-of", params={"patient": "demo-patient-001"}).json()
    assert body["asOf"].startswith("2026-09-19")
    assert body["latestAdministration"]
    assert "historical" in body["explanation"]


def test_token_check_reports_missing_credentials_without_crashing(client: TestClient):
    body = client.get("/api/token-check").json()
    assert body["ok"] is False
    assert "EPIC_BACKEND_CLIENT_ID" in body["reason"]


def test_interactive_login_is_unavailable_without_a_client_id(client: TestClient):
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 503


def test_auth_callback_rejects_an_unknown_state(client: TestClient):
    """State mismatch is the CSRF guard; it must not be skippable."""
    response = client.get("/api/auth/callback",
                          params={"code": "abc", "state": "forged"})
    assert response.status_code == 400
    assert "state" in response.json()["detail"].lower()


def test_no_secret_material_is_ever_returned(client: TestClient):
    """The Epic token must not leak into any client-visible payload."""
    text = client.get("/api/reconciliation",
                      params={"patient": "demo-patient-001"}).text
    for forbidden in ("access_token", "Bearer", "PRIVATE KEY", "client_assertion"):
        assert forbidden not in text


# ---------------------------------------------------------------------------------
# Fixture integrity
# ---------------------------------------------------------------------------------


def test_every_fixture_parses_and_reconciles():
    assert available_fixture_ids()
    for fixture_id in available_fixture_ids():
        bundle = load_fixture_bundle(fixture_id)
        rows, as_of = reconcile_bundle(bundle)
        assert bundle.patient.id, f"{fixture_id} has no patient id"
        assert rows, f"{fixture_id} produced no reconciliation rows"
        assert as_of is not None


def test_fixtures_are_labelled_as_synthetic():
    """The honesty requirement: demo data must never present itself as Epic data."""
    for fixture_id in available_fixture_ids():
        bundle = load_fixture_bundle(fixture_id)
        assert "Synthetic" in bundle.source_note
        assert bundle.source == "fixtures"
