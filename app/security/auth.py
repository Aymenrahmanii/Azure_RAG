"""Bearer-token authentication for the API.

Tokens are RS256 JWTs. The signing key comes from one of two places, chosen by configuration:
  * a static PEM public key: tokens minted by `python -m app.security.mint` (the private key stays
    with the operator and never reaches the cloud), used while the tenant forbids app registrations;
  * a JWKS URL: Microsoft Entra ID (https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys).
Moving to Entra later is configuration only: set the JWKS URL, issuer and audience.
"""

from dataclasses import dataclass, field

import jwt

ALGORITHMS = ["RS256"]  # explicit allow-list: never "none", never HS256 with a public key


class TokenError(Exception):
    """The token is missing, malformed, expired, or not meant for this API."""


@dataclass(frozen=True)
class User:
    id: str
    groups: frozenset[str] = field(default_factory=frozenset)
    name: str = ""


class TokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        public_key: str = "",
        jwks_url: str = "",
        leeway_seconds: int = 30,
    ):
        if bool(public_key) == bool(jwks_url):
            raise ValueError("configure exactly one of a public key or a JWKS URL")
        if not issuer or not audience:
            raise ValueError(
                "issuer and audience are required: an unchecked audience accepts any token"
            )
        self.issuer, self.audience, self.leeway = issuer, audience, leeway_seconds
        self._static_key = public_key.replace("\\n", "\n") if public_key else None
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600) if jwks_url else None

    def _key(self, token: str):
        if self._static_key is not None:
            return self._static_key
        return self._jwks.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> User:
        try:
            claims = jwt.decode(
                token,
                self._key(token),
                algorithms=ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise TokenError(str(exc)) from exc
        # `roles` is how Entra app roles arrive; `groups` is the self-issued claim.
        groups = claims.get("groups") or claims.get("roles") or []
        if not isinstance(groups, list):
            raise TokenError("groups claim must be a list")
        return User(str(claims["sub"]), frozenset(map(str, groups)), str(claims.get("name", "")))
