# Inpatient Medication Reconciliation Dashboard

A clinician-facing dashboard that reads inpatient encounter and medication data from
**Epic's public FHIR R4 sandbox** and reconciles what was *ordered* against what was
actually *given* — flagging doses that are on time, overdue, or missed.

> **Honesty statement.** This is a self-directed learning project built against Epic's
> **public FHIR sandbox** using published test patients. It contains **no PHI**, has **no
> production Epic connection**, and **no Epic certification is claimed**. The demo data
> shipped in `app/fixtures/` is **synthetic** — hand-authored FHIR-shaped resources, not
> captured from Epic — and the dashboard labels it as such on screen.

<!-- Add the demo GIF here once recorded: ![demo](docs/demo.gif) -->

**Live demo:** _add your Vercel URL after deploying_

---

## Why this problem

`MedicationRequest` is the order. `MedicationAdministration` is what actually reached the
patient. The gap between them is a patient-safety signal: a scheduled antibiotic with no
administration record is either a documentation failure or a missed dose, and on an
inpatient unit both matter.

This project reconciles the two resources and makes that gap visible.

## Architecture

```
Browser (React + TypeScript, Vite)
      |  HTTPS, JSON, same origin
      v
FastAPI service  (Vercel Python Function, /api)
      |  OAuth2 — SMART on FHIR
      v
Epic public sandbox — fhir.epic.com (FHIR R4)
```

The browser never talks to Epic. The access token is obtained, cached, and used entirely
server-side, which is both the correct security posture and the reason no CORS
configuration exists anywhere in this repo.

| Layer | File | Responsibility |
|---|---|---|
| Config | `app/config.py` | Environment-driven settings |
| Auth | `app/epic_auth.py` | Both SMART flows, token cache |
| Reads | `app/fhir_client.py` | FHIR searches, pagination, fallback |
| Parsing | `app/models.py` | FHIR R4 → domain dataclasses |
| **Logic** | `app/reconcile.py` | **The reconciliation engine — pure, no I/O** |
| API | `app/routes.py` | HTTP surface consumed by the UI |
| UI | `web/src/` | React dashboard |

`app/reconcile.py` performs no network calls and reads no clock it wasn't handed, so the
clinical logic is fully deterministic and unit-testable.

## FHIR resources used

| Resource | Search | Why it matters clinically |
|---|---|---|
| `Patient` | `Patient/{id}` | Who the chart belongs to |
| `Encounter` | `?patient={id}&class=IMP` | Scopes to the **inpatient** stay; admission time anchors the dosing schedule |
| `MedicationRequest` | `?patient={id}` | The order: drug, dose, and how often it should be given |
| `MedicationAdministration` | `?patient={id}&request={orderId}` | The dose that actually reached the patient |
| `Observation` | `?patient={id}&category=vital-signs` | Clinical context beside the medication chart |

Epic constrains `MedicationAdministration` search, so the client fetches orders first and
then fans out one administration query per order, concurrently.

## Reconciliation rules

For each active, non-PRN inpatient order:

1. **Derive the dosing interval** from `dosageInstruction[0].timing.repeat`
   (frequency / period / periodUnit). If that is absent, fall back to a shorthand code
   (`BID`, `Q8H`, `QHS`…). If neither yields an interval, the order is reported as
   `UNSCHEDULED` rather than guessed at.
2. **Build the expected schedule** from the later of `authoredOn` and admission, stepping
   by the interval until the earliest of order expiry, discharge, and the as-of time.
3. **Match administrations to expected doses** by nearest time within a grace window
   (default 60 minutes, capped at half the dosing interval). Each administration can
   satisfy at most one scheduled dose.
4. **Classify each expected dose:**

| Status | Meaning |
|---|---|
| **On time** | Administered within the grace window |
| **Overdue** | Due, no administration yet, within 2× the interval |
| **Missed** | Due, no administration, beyond 2× the interval |
| **PRN** | As-needed order — reported, never flagged |
| **Inactive** | Order no longer active; no further doses expected |
| **Unscheduled** | No derivable interval, or a dose given outside every expected window |

Linking prefers `MedicationAdministration.request`. When that reference is absent, the
engine falls back to matching on medication code within the same encounter and labels the
row **inferred link** — the UI says so rather than implying a certainty the data doesn't
support.

### The as-of decision (read this one)

Sandbox data is **static and historical**. Comparing it against wall-clock `now` would
mark every dose `MISSED` and make a working dashboard look broken.

So the engine evaluates against the **latest clinical timestamp in the record** — the
moment the chart was last true — rather than `datetime.now()`. The UI states which
timestamp is in effect, `/api/debug/as-of` explains the choice, and the `as_of` query
parameter overrides it to review the chart at any earlier point in the stay (doses
recorded after that point are correctly hidden).

This is a deliberate demo-correctness decision, not a fudge.

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt

# Terminal 1 — API
uvicorn api.index:app --reload --port 8000

# Terminal 2 — UI (proxies /api to port 8000)
cd web && npm install && npm run dev
```

Open <http://localhost:5173>. With no Epic credentials configured it runs on the committed
synthetic fixtures and says so on screen.

### Tests

```bash
pytest                    # 103 tests, no network required
cd web && npm run build   # type-check and bundle
```

## Connecting to the real Epic sandbox

1. **Generate a keypair:** `python scripts/generate_keys.py`
   Writes `private.pem` (secret, gitignored) and `public/.well-known/jwks.json` (public,
   commit it).
2. **Register two apps** at <https://fhir.epic.com> → Build Apps:
   - a **backend services** app — set its JWK Set URL to
     `https://<your-domain>/.well-known/jwks.json`
   - a **standalone launch** app — redirect URI `https://<your-domain>/api/auth/callback`

   Request R4 read scopes for Patient, Encounter, MedicationRequest,
   MedicationAdministration, and Observation.
3. **Set the environment variables** from `.env.example`.
4. **Verify the token flow:** `curl localhost:8000/api/token-check` — it reports success or
   Epic's own error text, without ever printing the token.
5. **Find a patient with real data:** `python scripts/capture_fixtures.py --probe`
   then capture the richest one with `--patient <id>`.

Epic's sandbox carries few `MedicationAdministration` records, so step 5 matters. If no
test patient has any, keep the synthetic fixtures and say so — the dashboard already does.

### Auth flows

Both SMART on FHIR flows are implemented:

- **Backend services** (`client_credentials` + an RS384-signed JWT assertion) — the
  default, so the public demo renders for anyone who opens it with no login.
- **Standalone launch** (`authorization_code` + PKCE) — at `/api/auth/login`, the flow a
  real patient-facing SMART app uses.

## Deploying

See [`DEPLOYMENT.md`](DEPLOYMENT.md). Short version: one Vercel Hobby project ($0, no
card) serves both the React build and the FastAPI function from a single domain.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness and non-secret configuration |
| `GET /api/patients` | Selectable patients |
| `GET /api/reconciliation` | The chart. Params: `patient`, `encounter`, `as_of`, `status`, `grace_minutes`, `miss_multiplier`, `inpatient_only` |
| `GET /api/token-check` | Diagnose the Epic token flow |
| `GET /api/debug/as-of` | Explain the chosen evaluation timestamp |
| `GET /api/auth/login` | Begin interactive SMART login |

## Security notes

- `private.pem` and `.env` are gitignored. `EPIC_PRIVATE_KEY` is a real signing key —
  anyone holding it can mint tokens as this app. If one is ever committed, rotate it:
  new keypair, new `jwks.json`, redeploy, update the Epic registration.
- The access token never appears in any client-visible payload; a test asserts this.
- `jwks.json` is public key material and is meant to be committed and publicly fetchable.

## Project documents

- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — scope, milestones, risks, acceptance criteria
- [`BUILD_INSTRUCTIONS.md`](BUILD_INSTRUCTIONS.md) — step-by-step build guide
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — free hosting options and deploy steps
