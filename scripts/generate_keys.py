"""Generate the RSA keypair and JWK Set for Epic's backend-services flow.

Run once, before registering the backend app at https://fhir.epic.com.

    python scripts/generate_keys.py

Writes:
    private.pem                      the signing key. GITIGNORED. Never commit it.
    public/.well-known/jwks.json     the public key set Epic fetches to verify signatures.

Then put the escaped private key into EPIC_PRIVATE_KEY, and register the deployed URL
https://<your-domain>/.well-known/jwks.json as the app's JWK Set URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_KEY_PATH = ROOT / "private.pem"
JWKS_PATH = ROOT / "public" / ".well-known" / "jwks.json"


def to_jwk(public_key, kid: str) -> dict:
    """Render an RSA public key as a JWK, per RFC 7517."""
    import base64

    numbers = public_key.public_numbers()

    def b64(value: int) -> str:
        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).decode().rstrip("=")

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS384",
        "n": b64(numbers.n),
        "e": b64(numbers.e),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kid", default="epic-recon-1",
                        help="Key ID; must match EPIC_JWK_KID (default: epic-recon-1)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing private.pem")
    args = parser.parse_args()

    if PRIVATE_KEY_PATH.exists() and not args.force:
        print(f"{PRIVATE_KEY_PATH} already exists. Re-run with --force to replace it.")
        print("Replacing the key requires re-registering the JWKS URL with Epic.")
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    PRIVATE_KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))

    JWKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    JWKS_PATH.write_text(
        json.dumps({"keys": [to_jwk(key.public_key(), args.kid)]}, indent=2) + "\n",
        encoding="utf-8",
    )

    escaped = PRIVATE_KEY_PATH.read_text(encoding="utf-8").replace("\n", "\\n")

    print(f"Wrote {PRIVATE_KEY_PATH}  (gitignored — keep it secret)")
    print(f"Wrote {JWKS_PATH}")
    print()
    print("Next steps:")
    print(f"  1. Set EPIC_JWK_KID={args.kid}")
    print("  2. Set EPIC_PRIVATE_KEY to the single-line value below")
    print("  3. Register https://<your-domain>/.well-known/jwks.json as the app's JWKS URL")
    print()
    print("EPIC_PRIVATE_KEY=" + escaped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
