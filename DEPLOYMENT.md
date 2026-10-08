# Deployment — Going Live for Free

## Recommendation

**Vercel Hobby (free), one project, frontend + FastAPI in the same repo.**

Why this one:

- Frontend and backend share an origin, so there is no CORS setup and no second deploy.
- Vercel's Python runtime runs a FastAPI ASGI app directly from `api/index.py`.
- Static files in `public/` are served at the domain root, which is exactly what the Epic
  backend-services flow needs for `/.well-known/jwks.json`.
- Free HTTPS, free `*.vercel.app` domain, automatic deploys on push to `main`.
- The spec already targets Vercel, and the repo has the Vercel tooling available.

Cost: $0. The Hobby tier prohibits commercial use; a portfolio/demo project is fine.

---

## Free options compared

| Platform | Free tier | Cold start | Always on | Fits this project |
|---|---|---|---|---|
| **Vercel Hobby** | Yes, no card | ~1-2s on Python functions | Serverless (scales to zero) | **Best** — one deploy, static + Python together |
| Render free web service | Yes | ~30-60s after 15 min idle | Sleeps | Workable, but a minute-long wake-up in a live interview is painful |
| Hugging Face Spaces (Docker) | Yes | None once warm | Yes, until idled | Good backend fallback; the URL looks less like a product |
| Fly.io | Small free allowance, card required | Fast | Yes | Fine, but card + more ops work |
| Railway / Heroku | Trial credit only | — | — | Not durably free |
| GitHub Pages / Netlify | Static only | — | — | Frontend only; cannot host FastAPI |

**Fallback plan if Vercel's Python runtime fights you:** frontend on Vercel, backend as a
Docker Space on Hugging Face, and set `VITE_API_BASE` to the Space URL with CORS allowing
only the Vercel domain. Keep JWKS served from the Vercel domain and register that URL.

---

## Vercel setup

### 1. `vercel.json`

```json
{
  "buildCommand": "cd web && npm install && npm run build",
  "outputDirectory": "web/dist",
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/index" }
  ]
}
```

`api/index.py` must expose a module-level `app` (the FastAPI instance). Dependencies go in
`api/requirements.txt`, kept minimal:

```
fastapi
httpx
pyjwt[crypto]
python-dateutil
```

Do not add `uvicorn` to that file — Vercel provides the server. Keep it in your local dev
requirements only.

### 2. Push and import

```bash
git init
git add .
git commit -m "feat: epic fhir medication reconciliation dashboard"
git branch -M main
git remote add origin https://github.com/<you>/Epic-Inpatient-Medication-Reconciliation-Dashboard.git
git push -u origin main
```

Then import the repo at <https://vercel.com/new>, or run `vercel` from the project root
(the `vercel:deploy` skill in this session can drive it).

### 3. Environment variables

Set these in Vercel → Project → Settings → Environment Variables (Production + Preview):

| Name | Value |
|---|---|
| `EPIC_FHIR_BASE` | `https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4` |
| `EPIC_TOKEN_URL` | `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token` |
| `EPIC_AUTHORIZE_URL` | `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize` |
| `EPIC_BACKEND_CLIENT_ID` | non-production client ID of the backend app |
| `EPIC_PATIENT_CLIENT_ID` | non-production client ID of the standalone app |
| `EPIC_PRIVATE_KEY` | full contents of `private.pem`, newlines as `\n` |
| `EPIC_JWK_KID` | `epic-recon-1` (must match `jwks.json`) |
| `DEMO_MODE` | `live` or `fixtures` |
| `DEFAULT_PATIENT_ID` | the sandbox patient chosen in Step 3 |

**Never commit these.** `EPIC_PRIVATE_KEY` is a real signing key: anyone holding it can
mint tokens as your app. Keep `*.pem` and `.env` in `.gitignore`, and if the key is ever
pushed to a public repo, rotate it — generate a new pair, replace `jwks.json`, redeploy,
and update the Epic app registration.

### 4. Register the real URLs with Epic

After the first successful deploy you have a permanent domain. Go back to the Epic app
registrations and set:

- Backend app JWKS URL: `https://<domain>/.well-known/jwks.json`
- Patient app redirect URI: `https://<domain>/api/auth/callback`

Epic fetches the JWKS itself, so it must be publicly reachable with no auth in front of it.
Open it in a private browser window to confirm.

### 5. Post-deploy checks

- [ ] `https://<domain>/api/health` returns `{"ok": true}`
- [ ] `https://<domain>/.well-known/jwks.json` loads in a logged-out browser
- [ ] `https://<domain>/api/reconciliation?patient=<id>` returns rows
- [ ] The dashboard renders with no login and no console errors
- [ ] No token, key, or client secret appears in the browser network tab or JS bundle
- [ ] Setting `DEMO_MODE=fixtures` still renders the dashboard (your interview safety net)

---

## Keeping the demo reliable

The live sandbox is a third-party dependency that can be slow or unavailable at the worst
moment. Two cheap protections:

1. **Fixtures fallback.** If an Epic call fails or times out, serve the committed fixture
   snapshot and show a visible banner saying so. Never show an empty screen.
2. **Short timeouts.** Cap Epic calls at ~8s (`httpx.Timeout`) so a hung upstream cannot
   hit the platform's function timeout and return an opaque platform error page.
