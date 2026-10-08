"""HTTP API consumed by the React dashboard.

The browser only ever talks to this service, never to Epic. The Epic access token stays
server-side, which is both the correct security posture and what removes any need for
CORS configuration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.config import get_settings
from app.epic_auth import (
    EpicAuthError,
    build_authorize_url,
    exchange_authorization_code,
    generate_pkce_pair,
    get_access_token,
    new_state,
)
from app.fhir_client import FhirError, load_bundle
from app.fixtures_loader import FixtureNotFound, fixture_patients
from app.models import DoseStatus, parse_dt
from app.reconcile import reconcile_bundle, summarize

log = logging.getLogger(__name__)
router = APIRouter()

# Short-lived PKCE state for the interactive flow. Single-process only; a multi-instance
# deployment would move this to signed cookies or a shared store.
_pending_logins: dict[str, str] = {}


@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus a non-secret view of how the service is configured."""
    settings = get_settings()
    return {
        "ok": True,
        "version": __version__,
        "demoMode": settings.demo_mode,
        "backendAuthConfigured": settings.backend_auth_configured,
        "patientAuthConfigured": settings.patient_auth_configured,
        "fhirBase": settings.fhir_base,
    }


@router.get("/patients")
async def list_patients() -> dict[str, object]:
    """Patients the dashboard can display.

    In fixture mode this is the committed set; in live mode the configured sandbox
    patient is listed alongside them so both are reachable from the picker.
    """
    settings = get_settings()
    patients = []
    try:
        patients = fixture_patients()
    except FixtureNotFound:
        log.warning("No fixtures available for the patient list.")

    known = {p["id"] for p in patients}
    if settings.backend_auth_configured and settings.default_patient_id not in known:
        patients.insert(0, {
            "id": settings.default_patient_id,
            "name": "Epic sandbox patient",
            "birthDate": "",
            "gender": "",
        })

    return {"patients": patients, "defaultPatientId": _default_patient_id()}


def _default_patient_id() -> str:
    settings = get_settings()
    if settings.backend_auth_configured and not settings.use_fixtures:
        return settings.default_patient_id
    try:
        from app.fixtures_loader import default_fixture_id

        return default_fixture_id()
    except FixtureNotFound:
        return settings.default_patient_id


@router.get("/reconciliation")
async def reconciliation(
    patient: str | None = Query(default=None, description="FHIR Patient logical id"),
    encounter: str | None = Query(default=None, description="Filter to one encounter"),
    as_of: str | None = Query(default=None, description="ISO timestamp to evaluate against"),
    status: str | None = Query(default=None, description="Comma-separated status filter"),
    grace_minutes: int | None = Query(default=None, ge=0, le=720),
    miss_multiplier: float | None = Query(default=None, gt=0, le=20),
    inpatient_only: bool = Query(default=True),
) -> dict[str, object]:
    """Reconcile a patient's medication orders against their administrations."""
    settings = get_settings()
    patient_id = patient or _default_patient_id()

    override = parse_dt(as_of) if as_of else None
    if as_of and override is None:
        raise HTTPException(status_code=400, detail=f"Unparseable as_of value: {as_of!r}")

    try:
        bundle = await load_bundle(patient_id)
    except EpicAuthError as exc:
        raise HTTPException(status_code=502, detail=f"Epic authentication failed: {exc}")
    except FhirError as exc:
        raise HTTPException(status_code=502, detail=f"Epic FHIR read failed: {exc}")
    except FixtureNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    grace = timedelta(minutes=grace_minutes if grace_minutes is not None
                      else settings.grace_minutes)
    multiplier = (miss_multiplier if miss_multiplier is not None
                  else settings.miss_multiplier)

    rows, effective_as_of = reconcile_bundle(
        bundle,
        as_of=override,
        grace=grace,
        miss_multiplier=multiplier,
        inpatient_only=inpatient_only,
    )

    if encounter:
        rows = [r for r in rows if r.encounter_id == encounter]

    if status:
        wanted = {s.strip().upper() for s in status.split(",") if s.strip()}
        unknown = wanted - {s.value for s in DoseStatus}
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown status filter values: {sorted(unknown)}",
            )
        rows = [r for r in rows if r.status.value in wanted]

    return {
        "patient": {
            "id": bundle.patient.id,
            "name": bundle.patient.name,
            "birthDate": bundle.patient.birth_date,
            "gender": bundle.patient.gender,
        },
        "encounters": [
            {
                "id": enc.id,
                "label": enc.label,
                "status": enc.status,
                "classCode": enc.class_code,
                "start": enc.start.isoformat() if enc.start else None,
                "end": enc.end.isoformat() if enc.end else None,
            }
            for enc in bundle.encounters
        ],
        "vitals": [
            {
                "id": vital.id,
                "label": vital.label,
                "value": vital.value,
                "recordedAt": vital.recorded_at.isoformat() if vital.recorded_at else None,
            }
            for vital in bundle.vitals
        ],
        "rows": [row.to_dict() for row in rows],
        "summary": summarize(rows),
        "asOf": effective_as_of.isoformat(),
        "asOfSource": "override" if override else "latest-clinical-timestamp",
        "dataSource": bundle.source,
        "dataSourceNote": bundle.source_note,
        "settings": {
            "graceMinutes": int(grace.total_seconds() // 60),
            "missMultiplier": multiplier,
            "inpatientOnly": inpatient_only,
        },
    }


@router.get("/token-check")
async def token_check() -> JSONResponse:
    """Diagnose the backend-services token flow without exposing the token itself."""
    settings = get_settings()
    if not settings.backend_auth_configured:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "reason": "EPIC_BACKEND_CLIENT_ID / EPIC_PRIVATE_KEY are not configured.",
            },
        )
    try:
        token = await get_access_token(force_refresh=True)
    except EpicAuthError as exc:
        return JSONResponse(status_code=200, content={"ok": False, "reason": str(exc)})

    return JSONResponse(content={
        "ok": True,
        "scope": token.scope,
        "expiresInSeconds": max(0, int(token.expires_at - _now())),
        "tokenPreview": f"{token.value[:6]}...{token.value[-4:]}",
    })


def _now() -> float:
    import time

    return time.time()


@router.get("/auth/login")
async def auth_login() -> RedirectResponse:
    """Begin the interactive SMART standalone launch."""
    settings = get_settings()
    if not settings.patient_auth_configured:
        raise HTTPException(
            status_code=503,
            detail="Interactive login is not configured (EPIC_PATIENT_CLIENT_ID missing).",
        )
    verifier, challenge = generate_pkce_pair()
    state = new_state()
    _pending_logins[state] = verifier
    return RedirectResponse(build_authorize_url(settings, challenge, state))


@router.get("/auth/callback")
async def auth_callback(request: Request) -> dict[str, object]:
    """Complete the interactive flow by exchanging the authorization code."""
    params = request.query_params
    if error := params.get("error"):
        raise HTTPException(
            status_code=400,
            detail=f"Epic returned an error: {error} {params.get('error_description', '')}",
        )

    code = params.get("code")
    state = params.get("state") or ""
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code in callback.")

    verifier = _pending_logins.pop(state, None)
    if verifier is None:
        # A mismatched state means the callback did not originate from our /auth/login.
        raise HTTPException(status_code=400, detail="Unknown or expired login state.")

    try:
        token = await exchange_authorization_code(code, verifier)
    except EpicAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # The token deliberately stays server-side; only non-secret facts go back.
    return {
        "ok": True,
        "scope": token.scope,
        "patient": token.patient,
        "message": "Interactive SMART on FHIR login succeeded.",
    }


@router.get("/debug/as-of")
async def debug_as_of(patient: str | None = None) -> dict[str, object]:
    """Explain which timestamp the engine chose and why.

    Sandbox data is historical, so this endpoint exists to make the as-of decision
    inspectable rather than magical.
    """
    bundle = await load_bundle(patient or _default_patient_id())
    _, as_of = reconcile_bundle(bundle)
    latest_admin = max(
        (a.given_at for a in bundle.administrations if a.given_at),
        default=None,
    )
    return {
        "asOf": as_of.isoformat(),
        "wallClockNow": datetime.now(tz=as_of.tzinfo).isoformat(),
        "latestAdministration": latest_admin.isoformat() if latest_admin else None,
        "explanation": (
            "Sandbox data is static and historical. Evaluating against wall-clock now "
            "would mark every dose MISSED, so the engine evaluates against the latest "
            "clinical timestamp in the record unless an as_of override is supplied."
        ),
    }
