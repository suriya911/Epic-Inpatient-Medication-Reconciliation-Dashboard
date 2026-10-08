# Project Plan — Epic Inpatient Medication Reconciliation Dashboard

Status: draft v1
Owner: suriya911
Source spec: `Epic_FHIR_Medication_Dashboard_Project_Spec.md`

---

## 1. Goal

Ship a publicly reachable, clinician-facing dashboard that reads inpatient encounter and
medication data from Epic's public FHIR R4 sandbox and reconciles each medication order
against its administration records, flagging doses that are **On Time**, **Overdue**, or
**Missed**.

Success = a live URL an interviewer can open, plus a public GitHub repo whose README
explains the FHIR resources used and the clinical reason reconciliation matters.

## 2. Non-goals

- No Epic Willow integration, no Epic certification claim, no production Epic connection.
- No real PHI. Sandbox test patients only.
- No persistent database in v1 (stateless reads + in-process cache).
- No multi-user accounts or role-based access in v1.

## 3. Architecture

```
Browser (React SPA, Vercel)
      |  HTTPS, JSON
      v
FastAPI service (Vercel Python Function, same repo, /api)
      |  OAuth2 (SMART on FHIR)
      v
Epic public sandbox: https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
```

Single repository, single deploy target, single domain. This avoids CORS entirely: the
browser only ever talks to our own origin, and the Epic access token never leaves the
server.

Data flow per request:

1. Server obtains an Epic access token (cached in memory until ~60s before expiry).
2. Server fetches `Encounter`, `MedicationRequest`, `MedicationAdministration`, `Patient`,
   `Observation` for the selected patient/encounter.
3. Server runs the reconciliation engine (pure function, fully unit-testable).
4. Server returns a flat list of reconciliation rows; React renders and filters them.

Reconciliation runs **server-side** so the logic is testable without a browser and the
frontend stays a dumb renderer.

## 4. Auth decision

Two SMART on FHIR flows are possible against the sandbox:

| Flow | Pros | Cons | Verdict |
|---|---|---|---|
| Standalone patient launch (authorization_code + PKCE) | Demonstrates the full interactive SMART flow; the flow a real app uses | Every visitor must type Epic test-patient MyChart credentials; breaks a cold demo | Build it, keep it as the "Sign in with Epic" path |
| Backend services (`client_credentials` + signed JWT assertion) | No human login; the live demo just works for anyone opening the URL | Does not show the interactive flow; needs a published JWKS | **Primary path for the live demo** |

Plan: implement **backend services as the default** so the public URL renders data with
zero interaction, and implement the **authorization_code flow as a second, optional login
button**. Being able to explain both out loud is itself an interview asset.

Key management for backend services: generate an RSA keypair, publish the public JWK set
as a static file served from the same deployment (e.g. `/.well-known/jwks.json`), register
that URL on the Epic app, keep the private key only in an environment variable.

## 5. Data model and field mapping

| FHIR resource | Fields consumed | Used for |
|---|---|---|
| `Patient` | `id`, `name`, `birthDate`, `gender` | Row label, header |
| `Encounter` (`class=IMP`) | `id`, `period.start`, `period.end`, `status`, `subject` | Inpatient scoping, filter |
| `MedicationRequest` | `id`, `status`, `intent`, `medicationCodeableConcept` / `medicationReference`, `authoredOn`, `dosageInstruction[0].timing.repeat`, `dosageInstruction[0].asNeededBoolean`, `dispenseRequest.validityPeriod` | The order: what, how often, from when |
| `MedicationAdministration` | `id`, `status`, `effectiveDateTime` / `effectivePeriod.start`, `request`, `dosage.dose` | What was actually given |
| `Observation` | `code`, `effectiveDateTime`, `valueQuantity` | Vitals context panel (secondary) |

Linking rule: prefer `MedicationAdministration.request` to `MedicationRequest.id`. Fall back
to matching on medication code + encounter when the reference is absent, and mark such rows
as `link: "inferred"` so the UI can be honest about it.

## 6. Reconciliation rules

Inputs: one order, its administrations, an **as-of timestamp**.

1. **Derive the dosing interval.** From `timing.repeat.frequency` / `period` / `periodUnit`
   (e.g. frequency 2, period 1, periodUnit "d" means every 12h). If absent, map the timing
   code or text (`BID`, `TID`, `Q8H`, `QD`) to hours via a lookup table. If still unknown,
   the order is `UNSCHEDULED`: reported but not flagged.
2. **Skip what should not be flagged.** `asNeededBoolean = true` (PRN) gives status `PRN`.
   Order status not in {`active`, `on-hold`} gives status `INACTIVE`.
3. **Build the expected schedule.** Start at the later of `authoredOn` and
   `encounter.period.start`; step forward by the interval up to the as-of time.
4. **Match administrations to expected doses** within `interval/2` (capped at the grace
   window), each administration consumed at most once.
5. **Classify:**
   - `ON_TIME` — matched within the grace window (default 60 min, configurable).
   - `OVERDUE` — expected dose has no match, and `as_of - expected <= miss_threshold`
     (default 2x interval).
   - `MISSED` — expected dose has no match and `as_of - expected > miss_threshold`, or the
     order has zero administrations and its window has fully elapsed.

### The as-of problem (important)

Sandbox data is static and historical. Comparing it against wall-clock `now` marks every
order `MISSED` and the dashboard looks broken. So `as_of` defaults to the **latest clinical
timestamp present in the fetched dataset** (max of administration times and encounter end),
not `datetime.now()`. It is overridable via query param, and the UI shows which as-of time
is in effect. This is a deliberate demo-correctness decision, documented in the README —
not a fudge, and worth being able to explain.

### Sparse-data fallback

Epic's sandbox may return few or no `MedicationAdministration` records for a given test
patient. Mitigation, in order:

1. Probe several published test patients during build and pick the richest.
2. Ship a `DEMO_MODE=fixtures` path that replays a captured, committed JSON snapshot of real
   sandbox responses through the identical reconciliation engine. Label it clearly in the UI
   ("Fixture data captured from Epic sandbox on <date>"). This keeps the live demo alive
   when the sandbox is slow, rate-limited, or down mid-interview.

## 7. Milestones

| # | Milestone | Deliverable | Est. |
|---|---|---|---|
| M0 | Repo + skeleton | Repo, FastAPI `/api/health`, Vite React shell, both running locally | 30 min |
| M1 | Epic app registered | `client_id`, JWKS URL registered, redirect URI set | 30 min |
| M2 | Token in hand | Server obtains a valid access token via backend services flow | 45 min |
| M3 | Raw FHIR reads | `/api/patients`, `/api/encounters`, `/api/meds` return real sandbox JSON; snapshot committed as fixtures | 1.5 hr |
| M4 | Reconciliation engine | Pure module + unit tests covering on-time / overdue / missed / PRN / no-timing | 1.5 hr |
| M5 | Dashboard UI | Color-coded table, patient + encounter filters, status filter, as-of banner | 2.5 hr |
| M6 | Deploy | Live URL on Vercel, env vars set, JWKS reachable | 45 min |
| M7 | Document + demo | README, architecture note, 30-60s GIF | 45 min |

Total roughly 9 hours: one long day, or two evenings. M4 is the piece with real engineering
substance; protect its time. M5 can degrade to a plainer table without hurting the story.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Sandbox has no `MedicationAdministration` for chosen patient | Core feature shows nothing | Probe multiple patients (M3); fixtures fallback |
| Epic app activation delay after registration | Blocks M2 | Register in M1 **first**, build M0/M4 while waiting |
| Backend-services JWT auth misconfigured (key, `kid`, `aud`, JWKS reachability) | Blocks all data | Verify the JWKS URL loads publicly before debugging the token call; log the exact Epic error body |
| Historical timestamps make everything MISSED | Demo looks broken | As-of logic (section 6) |
| Serverless cold start discards the token cache | Slow first load, extra Epic calls | Accept; keep the cache anyway, show a loading state |
| Vercel Python function size/timeout limits | Deploy failure | Keep deps minimal (`fastapi`, `httpx`, `pyjwt[crypto]`); no pandas |

## 9. Acceptance criteria

- [ ] Public URL loads and renders reconciliation rows with no login required.
- [ ] At least one row each of On Time, Overdue, Missed is demonstrable.
- [ ] Filter by patient and by encounter works.
- [ ] Reconciliation engine has unit tests covering all status branches; tests pass in CI.
- [ ] The Epic access token is never present in any client-side payload.
- [ ] README explains each FHIR resource used, the reconciliation rules, and the as-of decision.
- [ ] README states plainly: self-directed project against Epic's **public sandbox**; no Epic
      certification, no production Epic experience, no PHI. (Spec's honesty checklist.)

## 10. Demo script (60 seconds)

1. Open live URL; table renders, as-of banner visible. (5s)
2. Point at a red MISSED row, then the matching order detail. (15s)
3. Filter to one inpatient encounter. (10s)
4. Say the one-liner: *MedicationRequest is the order, MedicationAdministration is what was
   actually given; the gap between them is the safety signal.* (15s)
5. Cut to the architecture diagram in the README. (15s)
