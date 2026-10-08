# Build Instructions

Follow in order. Each step ends with something you can verify. Estimated total: ~9 hours.

Prerequisites: Python 3.12, Node 20+, Git, a GitHub account, a Vercel account (free Hobby
tier), and OpenSSL (ships with Git for Windows).

> Verify every Epic-specific URL, client ID, patient ID, and test credential against the
> live docs at <https://fhir.epic.com> as you go. Epic changes endpoints and sandbox data
> periodically; values written here are starting points, not guarantees.

---

## Step 0 — Repo skeleton (30 min)

Target layout:

```
.
├── api/
│   ├── index.py            # Vercel entrypoint; exposes FastAPI `app`
│   └── requirements.txt    # fastapi, httpx, pyjwt[crypto], python-dateutil
├── app/                    # Python package (importable + testable)
│   ├── __init__.py
│   ├── config.py           # env-var settings
│   ├── epic_auth.py        # token acquisition + cache
│   ├── fhir_client.py      # typed reads against the sandbox
│   ├── reconcile.py        # PURE logic, no I/O
│   └── fixtures/           # captured sandbox JSON (committed)
├── tests/
│   └── test_reconcile.py
├── web/                    # Vite + React frontend
│   ├── src/
│   └── package.json
├── public/
│   └── .well-known/jwks.json   # public key set for Epic backend services
├── vercel.json
├── PROJECT_PLAN.md
└── README.md
```

Commands:

```bash
python -m venv .venv && . .venv/Scripts/activate   # Git Bash on Windows
pip install fastapi "uvicorn[standard]" httpx "pyjwt[crypto]" python-dateutil pytest
npm create vite@latest web -- --template react-ts
cd web && npm install && cd ..
```

Minimal `api/index.py`:

```python
from fastapi import FastAPI
from app.routes import router

app = FastAPI(title="Epic Inpatient Med Reconciliation")
app.include_router(router, prefix="/api")

@app.get("/api/health")
def health():
    return {"ok": True}
```

**Verify:** `uvicorn api.index:app --reload` then `curl localhost:8000/api/health` returns
`{"ok":true}`, and `npm run dev` in `web/` serves the React shell.

---

## Step 1 — Register the Epic sandbox app (30 min, do this FIRST)

Registration can take time to activate. Start it, then continue with Steps 3-4 while waiting.

1. Create a free account at <https://fhir.epic.com> and open **Build Apps**.
2. Generate an RSA keypair locally (this is the backend-services key):
   ```bash
   openssl genrsa -out private.pem 2048
   openssl rsa -in private.pem -pubout -out public.pem
   ```
   `private.pem` must never be committed. Add `*.pem` to `.gitignore` now.
3. Convert `public.pem` into a JWK Set and save it at `public/.well-known/jwks.json` with a
   stable `kid` (any string; reuse the same one when signing). Use `jwcrypto` or an offline
   converter:
   ```bash
   pip install jwcrypto
   python -c "from jwcrypto import jwk; k=jwk.JWK.from_pem(open('public.pem','rb').read()); k.update(kid='epic-recon-1', use='sig', alg='RS384'); print('{\"keys\":[%s]}' % k.export(private_key=False))" > public/.well-known/jwks.json
   ```
4. Register **two** apps (Epic treats them separately):
   - **Backend app** — type: *Backend Services / system app*. Set the JWKS URL to
     `https://<your-vercel-domain>/.well-known/jwks.json`. Request these APIs (R4):
     `Patient.Read/Search`, `Encounter.Read/Search`, `MedicationRequest.Read/Search`,
     `MedicationAdministration.Read/Search`, `Observation.Read/Search`.
   - **Patient-facing app** — type: *standalone launch*, redirect URI
     `https://<your-vercel-domain>/api/auth/callback` plus `http://localhost:8000/api/auth/callback`
     for local work. Same API scopes.
5. Record both **non-production client IDs**.

**Verify:** you have two client IDs and, after Step 6 deploys, the JWKS URL loads publicly
in a browser (Epic must be able to fetch it — an unreachable JWKS is the single most common
cause of a failing token request).

---

## Step 2 — Get a token (45 min)

Sandbox endpoints (confirm against the docs):

- FHIR base: `https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4`
- Authorize: `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize`
- Token: `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token`

### 2a. Backend services (primary path)

Build a JWT assertion signed RS384 with the private key:

- header: `{"alg":"RS384","typ":"JWT","kid":"epic-recon-1"}`
- claims: `iss` = client_id, `sub` = client_id, `aud` = the token URL,
  `jti` = a fresh UUID, `exp` = now + 4 minutes (Epic rejects long-lived assertions).

POST form-encoded to the token URL:

```
grant_type=client_credentials
client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
client_assertion=<the signed JWT>
```

Cache the returned `access_token` in a module-level variable with its expiry, refreshing
60s early.

### 2b. Authorization code + PKCE (secondary path)

`/api/auth/login` redirects to the authorize URL with `response_type=code`, `client_id`,
`redirect_uri`, `scope`, `state`, `aud` = FHIR base, and PKCE `code_challenge` (S256).
`/api/auth/callback` exchanges the code at the token URL and stores the token in a signed,
`HttpOnly`, `Secure`, `SameSite=Lax` cookie. Published MyChart test credentials (for
example the "Camila Lopez" account) are listed in Epic's sandbox documentation.

**Verify:** a script prints a token with a non-empty `access_token` and an `expires_in`.
On failure, log Epic's full JSON error body — it names the actual problem.

---

## Step 3 — Pull FHIR data (1.5 hr)

Every request carries `Authorization: Bearer <token>` and `Accept: application/fhir+json`.

```
GET /Patient/{id}
GET /Encounter?patient={id}&class=IMP
GET /MedicationRequest?patient={id}&_count=50
GET /MedicationAdministration?patient={id}&request={medicationRequestId}
GET /Observation?patient={id}&category=vital-signs
```

Notes that will save you time:

- Epic constrains search parameters per resource. `MedicationAdministration` in particular
  often requires `patient` together with `request`, so the practical pattern is: fetch the
  requests first, then fan out one administration query per request ID. Run those fan-out
  calls concurrently with `httpx.AsyncClient` and `asyncio.gather`.
- Always follow `Bundle.link[rel=next]` for pagination; do not assume one page.
- Handle `Bundle.total = 0` as a normal, expected result, not an error.
- Probe several published sandbox test patients and keep the one with the most
  `MedicationAdministration` records. Record the chosen patient ID in `config.py`.

Then capture fixtures once the data looks good:

```bash
python -m app.capture_fixtures --patient <id> --out app/fixtures/
```

Commit those fixtures. They are sandbox test data, not PHI.

**Verify:** `/api/encounters?patient=<id>` returns real inpatient encounters, and at least
one `MedicationRequest` has a matching `MedicationAdministration`.

---

## Step 4 — Reconciliation engine (1.5 hr)

`app/reconcile.py` contains **no network calls**. Signature:

```python
def reconcile(
    orders: list[Order],
    administrations: list[Administration],
    as_of: datetime,
    grace: timedelta = timedelta(minutes=60),
    miss_multiplier: float = 2.0,
) -> list[ReconRow]: ...
```

Implement the rules in PROJECT_PLAN.md section 6. Timing lookup table to start with:

```python
CODE_TO_HOURS = {
    "QD": 24, "DAILY": 24, "BID": 12, "TID": 8, "QID": 6,
    "Q4H": 4, "Q6H": 6, "Q8H": 8, "Q12H": 12, "QHS": 24, "QOD": 48,
}
```

Compute `as_of` as the max clinical timestamp in the dataset unless the caller overrides it,
and return it alongside the rows so the UI can display it.

Tests in `tests/test_reconcile.py` must cover: on-time match; late-but-within-threshold
(OVERDUE); long-overdue (MISSED); order with zero administrations; PRN order; order with no
parseable timing; and an administration that could match two expected doses (must be
consumed once). Run with `pytest`.

**Verify:** all tests pass, and `/api/reconciliation?patient=<id>` returns a mixed set of
statuses.

---

## Step 5 — Dashboard UI (2.5 hr)

One page. Components:

- **Header** — patient name, MRN-style ID, encounter selector.
- **As-of banner** — "Evaluated as of <timestamp> (latest data in record)". Include a short
  tooltip explaining why, and a badge when fixtures are in use.
- **Summary tiles** — counts of On Time / Overdue / Missed.
- **Table** — Patient, Medication, Dose, Ordered Time, Expected Time, Administered Time,
  Status. Sort by status severity by default.
- **Filters** — encounter, status, free-text medication search.

Status colors: green On Time, amber Overdue, red Missed, grey PRN/Inactive. Do not rely on
color alone — pair each with a text label (accessibility, and it matters in clinical UI).

Frontend calls only `/api/*` on its own origin. No Epic credentials in frontend code, and
no Epic URLs in the bundle.

**Verify:** `npm run build` succeeds and the built app renders live API data locally.

---

## Step 6 — Deploy free (45 min)

See `DEPLOYMENT.md` for the platform comparison and the exact steps. Short version: one
Vercel project serving both the React build and the FastAPI function.

**Verify:** the public URL renders rows with no login, and
`https://<domain>/.well-known/jwks.json` loads.

---

## Step 7 — Document + record (45 min)

README must contain:

1. One-paragraph description and the live URL.
2. Architecture diagram (the ASCII one from PROJECT_PLAN.md is fine).
3. Table of FHIR resources used and why each matters clinically.
4. The reconciliation rules in plain English, including the as-of decision.
5. Local setup instructions.
6. The honesty statement, verbatim and prominent:
   > This is a self-directed learning project built against Epic's **public FHIR sandbox**
   > using published test patients. It contains no PHI, no production Epic connection, and
   > no Epic certification is claimed.

Record a 30-60s GIF (ScreenToGif on Windows) following the demo script in PROJECT_PLAN.md
section 10, and embed it at the top of the README.

---

## Ongoing conventions

- Commit at each milestone: `feat(auth): epic backend services token flow`.
- Never commit `.pem` files, tokens, or `.env`. Add a `.env.example` with empty values.
- Keep `app/reconcile.py` free of I/O so it stays trivially testable.
- Run `pytest` before each push.
