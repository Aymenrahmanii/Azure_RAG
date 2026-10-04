"""Operator tool: create a signing key pair and mint access tokens.

    python -m app.security.mint keygen --out .keys
    python -m app.security.mint mint --key .keys/private.pem --issuer https://azrag.example \
        --audience azrag-api --sub alice --groups finance --ttl-hours 8

The private key never leaves the operator's machine (`.keys/` is git-ignored); deploy only
`public.pem` (AUTH_PUBLIC_KEY). Anyone holding the private key can mint any identity.
"""

import argparse
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


def mint_token(
    private_key: str,
    issuer: str,
    audience: str,
    sub: str,
    groups: list[str],
    ttl_seconds: int,
    name: str = "",
    now: float | None = None,
) -> str:
    now = int(now if now is not None else time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
        "groups": groups,
        "name": name,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", type=Path, default=Path(".keys"))
    m = sub.add_parser("mint")
    m.add_argument("--key", type=Path, required=True)
    m.add_argument("--issuer", required=True)
    m.add_argument("--audience", required=True)
    m.add_argument("--sub", required=True)
    m.add_argument("--name", default="")
    m.add_argument("--groups", default="", help="comma separated")
    m.add_argument("--ttl-hours", type=float, default=8)
    args = p.parse_args()

    if args.cmd == "keygen":
        args.out.mkdir(parents=True, exist_ok=True)
        private, public = generate_keypair()
        (args.out / "private.pem").write_bytes(private)
        (args.out / "public.pem").write_bytes(public)
        print(f"wrote {args.out}/private.pem (keep secret) and {args.out}/public.pem")
        return
    groups = [g for g in args.groups.split(",") if g]
    token = mint_token(
        args.key.read_text(),
        args.issuer,
        args.audience,
        args.sub,
        groups,
        int(args.ttl_hours * 3600),
        args.name,
    )
    print(token)


if __name__ == "__main__":
    main()
