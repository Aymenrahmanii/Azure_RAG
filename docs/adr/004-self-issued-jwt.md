# 004: Self-issued RS256 tokens instead of Entra ID

**Status:** accepted (2026-10-04), expected to be superseded

**Context.** The API must require authentication and carry group claims for security trimming. The
student tenant forbids app registrations, so Entra cannot issue tokens for this API.

**Decision.** The verifier accepts a static public key or a JWKS URL. Today tokens are minted by the
operator (`python -m app.security.mint`); the API only holds the public key. Moving to Entra is a
configuration change (`AUTH_JWKS_URL`).

**Consequences.** No SSO, no per-token revocation (tokens are short-lived instead), and whoever holds
the private key can mint any identity (it stays in the git-ignored `.keys/`). Security-trimming and
verification tests exist, including an HS256 key-confusion forgery. The API refuses to start outside
`local` without authentication.
