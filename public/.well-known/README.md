# `/.well-known/`

`jwks.json` belongs in this directory. It is absent right now only because no keypair has
been generated yet. Generate one with:

```bash
python scripts/generate_keys.py
```

That writes two files with opposite handling:

| File | Secret? | Commit it? |
|---|---|---|
| `private.pem` (repo root) | **Yes** — anyone holding it can mint tokens as this app | **Never.** It is gitignored. Its contents go into the `EPIC_PRIVATE_KEY` environment variable. |
| `public/.well-known/jwks.json` | No — public key material by design | **Yes.** Vercel serves it from the repository, and Epic must be able to fetch it. |

After the first deploy, confirm `https://<your-domain>/.well-known/jwks.json` loads in a
logged-out browser window, then register that URL as the backend app's JWK Set URL at
<https://fhir.epic.com>.

An unreachable or mismatched JWKS is the single most common cause of a failing
`client_credentials` token request. If Epic returns `invalid_client`, check three things
in order: the URL loads publicly, the `kid` in `jwks.json` matches `EPIC_JWK_KID`, and
`private.pem` is the same key the JWKS was generated from.
