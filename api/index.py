"""ASGI entrypoint.

Vercel's Python runtime imports the module-level ``app`` from this file; locally the
same object is served by uvicorn. Keeping one entrypoint means local and deployed
behaviour cannot drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Vercel invokes this file directly, so the repository root is not necessarily on the
# import path. Adding it keeps `from app...` imports working in both environments.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app import __version__  # noqa: E402
from app.routes import router  # noqa: E402

app = FastAPI(
    title="Epic Inpatient Medication Reconciliation",
    version=__version__,
    description=(
        "Reconciles inpatient MedicationRequest orders against MedicationAdministration "
        "records from Epic's public FHIR R4 sandbox."
    ),
)

app.include_router(router, prefix="/api")


@app.get("/api")
async def api_root() -> JSONResponse:
    """Point a bare /api at the useful endpoints."""
    return JSONResponse({
        "service": "epic-inpatient-med-reconciliation",
        "version": __version__,
        "endpoints": [
            "/api/health",
            "/api/patients",
            "/api/reconciliation?patient=<id>",
            "/api/token-check",
            "/api/debug/as-of",
            "/api/auth/login",
        ],
    })
