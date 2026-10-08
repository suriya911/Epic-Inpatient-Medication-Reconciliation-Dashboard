# Epic Inpatient Medication Reconciliation Dashboard
### Project Spec — Build in a Day

## What it is
A clinician-facing dashboard that pulls inpatient encounter and medication data from **Epic's public FHIR sandbox** (no Epic customer subscription needed) and shows which medication orders have been administered on time, which are overdue, and which are missing an administration record entirely.

## Why this project
The job you're applying for requires hands-on Epic experience, specifically the Inpatient/Willow module. You can't replicate Willow itself (it's proprietary, closed-source), but you CAN build a real, working application against the same underlying clinical data model Epic exposes publicly through FHIR. This gives you an honest, specific story: *"I don't have Epic certification, but I built a project against Epic's own data model to learn the domain before applying."*

## Tech Stack
- **Backend:** Python + FastAPI
- **Frontend:** React
- **Auth:** OAuth2 / SMART on FHIR (Epic's standard auth flow)
- **Data source:** Epic's public sandbox (fhir.epic.com) — free, public test patients

---

## Step-by-Step Build Plan

### 1. Register a sandbox app (~15 min)
- Go to **fhir.epic.com** → "Build Apps"
- Create a free developer account
- Register a new app as a **SMART on FHIR backend or standalone app**
- You'll receive a `client_id` for OAuth — no real Epic customer relationship required

### 2. Get OAuth working (~30–45 min)
- Epic's sandbox uses the SMART on FHIR OAuth2 authorization flow
- Use Epic's published sandbox test patients (e.g., "Camila Lopez," "Theodore Mychart") to authenticate
- Exchange the auth code for an access token

### 3. Pull inpatient data (~1–2 hrs)
Hit these FHIR REST endpoints against the sandbox base URL:
| Resource | Purpose |
|---|---|
| `Patient` | Demographics |
| `Encounter?class=IMP` | Inpatient encounters specifically |
| `MedicationRequest` | Active medication orders |
| `MedicationAdministration` | What's actually been given |
| `Observation` | Vitals, for context |

### 4. Build the reconciliation logic (~1–2 hrs)
For each inpatient encounter:
- Match each `MedicationRequest` to its corresponding `MedicationAdministration` record(s)
- Flag any order with no matching administration inside the expected dosing window as **"Overdue"**
- Flag orders with no administration at all as **"Missed"**

### 5. Build the dashboard UI (~2–3 hrs)
A simple React table is enough:
| Patient | Medication | Ordered Time | Administered Time | Status |
|---|---|---|---|---|
| ... | ... | ... | ... | 🟢 On Time / 🟡 Overdue / 🔴 Missed |

Color-code status. Add a filter by encounter or patient. That's a complete, demoable product.

### 6. Deploy + document
- Push to GitHub (public repo)
- Deploy frontend to Vercel (same pattern as your other projects)
- Record a 30–60 second demo screen recording or GIF
- Write a short README explaining the FHIR resources used and why reconciliation matters clinically

---

## Resume Bullet (use only once built)
> Built a clinician-facing dashboard against Epic's public FHIR sandbox (Open Epic), reconciling inpatient MedicationRequest and MedicationAdministration records to flag missed or overdue doses. Implemented SMART on FHIR OAuth2 authentication and pulled Patient, Encounter, and Observation resources for full inpatient context.

## Honesty Checklist Before You Send Anything
- [ ] Code is pushed to a public GitHub repo
- [ ] The app actually runs and does what the bullet says
- [ ] You can explain FHIR, SMART on FHIR OAuth2, and the MedicationRequest/MedicationAdministration resources out loud, unprompted
- [ ] You are NOT claiming Epic certification or production Epic experience anywhere — only that you built a self-directed project against Epic's public sandbox
