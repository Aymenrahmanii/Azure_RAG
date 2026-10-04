# Security

What protects the API, how each control was checked, and what is still missing.

## Controls and the evidence for each

| Threat | Control | Checked by |
|---|---|---|
| Anonymous use, LLM budget burn | Bearer JWT required on `/chat` and `/pipelines` (RS256, issuer, audience, expiry, required claims) | `tests/test_security.py`, `tests/test_api_security.py`; live: 401 without or with a bad token |
| Forged tokens | Algorithm allow-list (`RS256` only): `alg: none` and the HS256-signed-with-the-public-key attack are rejected | tests build both forgeries by hand |
| Reading documents you may not see | **Security trimming** in every retrieval path (below) | unit tests per path, an end-to-end test against the real Azure index, and a mutation check |
| Running unauthenticated by mistake | Fail closed: outside `ENVIRONMENT=local` the API refuses to start unless `AUTH_MODE=jwt`; an ACL without authentication is also a startup error; Terraform refuses to deploy without a public key | `test_api_security.py` |
| One user draining the budget | Per-user sliding-window rate limit (429 + `Retry-After`) | tests; limit is per replica (see limits) |
| Personal data leaking into logs | The audit log never contains question or answer text, only a salted hash of the user id, the question length and a hash of the question | tests assert an IBAN in a question and an email in the user id never reach the log |
| Missing accountability | One structured audit line per request: user hash, pipeline, sections returned, tokens, latency, status | `app/security/audit.py` |
| Personal data pasted into questions | `redact_pii` masks emails, IBANs (mod-97 checked), cards (Luhn), international phone numbers and IPv4 addresses before search, the LLM or the logs; the user gets a `notice` event and the audit entry records only kinds and counts | `tests/test_pii.py` (masks, and leaves article numbers, dates and checksum-failing numbers alone), `tests/test_api_hardening.py` |
| Data leaving through rendered answers (markdown image or link smuggled in by a poisoned passage) | Answers and the source text shown to users pass a stream-safe sanitiser: images removed, links reduced to their text, raw HTML removed, brackets other than numeric citations unwrapped (which also defeats reference-style links) | `tests/test_sanitize.py`: every possible split of the stream, nested brackets, reference-style images; `eval.injection` measured 0 surviving exfiltration URLs |
| Poisoned documents entering the index | Ingestion scans each upload for injected instructions, text addressed to an AI system, exfiltration markup and invisible/bidi characters; a flagged file is quarantined (status `quarantined`, reasons recorded) and a previously indexed clean version stays in place | `tests/test_injection_scan.py`, including a check that the four real regulations produce zero findings |
| Browser-side attacks on API responses | `X-Content-Type-Options`, `Referrer-Policy`, `Content-Security-Policy: default-src 'none'` | live check |
| Secrets in code or images | No keys anywhere: managed identity for Azure services; only the **public** token key is deployed; the audit salt is a generated Container Apps secret | `.gitignore` (`.keys/`), Terraform |

## Tokens

The tenant does not allow app registrations, so Entra ID cannot issue tokens for this API. Tokens are
instead minted offline by the operator (`python -m app.security.mint`), signed with a private key that stays
on the operator's machine. The cloud only holds the public key. The verifier takes either a static public key
(now) or a JWKS URL, so moving to Entra ID later means setting `AUTH_JWKS_URL`, `AUTH_ISSUER` and
`AUTH_AUDIENCE`; no application code changes.

Limits of this model: no single sign-on, no per-token revocation (rotate the key to revoke everything, or
keep token lifetimes short), and whoever holds the private key can mint any identity.

## Security trimming

`ACL_RESTRICTED='{"dora": ["finance"]}'` makes DORA readable only by tokens whose `groups` claim contains
`finance`. At request time the API computes the sources the caller may see and stores them in a
`ContextVar` (`app/security/access.py`). Every path reads it:

| Path | How it is enforced |
|---|---|
| Dense search | `search.in(source, ...)` filter inside the Azure AI Search query (and `$in` for Chroma). An empty allow-list becomes `false`, never "no filter" |
| BM25 | restricted chunks are excluded before ranking |
| Graph expansion | candidate sections from restricted sources are dropped |
| Agent tools | `get_section` / `related_sections` answer for a restricted section exactly as for a nonexistent one, so even its existence does not leak; neighbour lists are filtered |
| Global search | a community summary mixes its sources, so it is shown only to callers who may read **all** of them |

The LLM never receives restricted text, which is the point: filtering the answer afterwards cannot be made
reliable. Verified end to end against the real index with two tokens: a token without the group got no DORA
passages in the baseline, graph or agent pipeline (the agent said it did not know); the `finance` token got
the DORA answer. A mutation check confirmed that disabling the visibility lookup in any of the four paths
makes the tests fail.

Granularity is one source document (a whole regulation). Per-chunk ACLs would need an `allowed_groups`
field in the index and ingestion. The deployed environment currently leaves `ACL_RESTRICTED` empty, because
all four regulations are public; the mechanism is tested and switched on by setting the variable.

## Known limits

- Rate limiting and the audit trail are per replica and logged to the console. A shared limiter (API
  Management or Redis) and shipping the audit stream to Log Analytics are the production answers.
- The UI takes a pasted token; there is no login flow.
- Network: the API is public (HTTPS) behind token authentication. Private endpoints for AI Search, Azure
  OpenAI and Storage need a VNet-integrated environment and a paid tier of some services.
- PII masking is regex plus checksums: it does not find names or free-form identifiers, and local phone
  numbers without an international prefix are left alone. It is data minimisation, not anonymisation.
- The scanner and the sanitiser are filters, not guarantees: a determined attacker can phrase an
  instruction the patterns do not know. Prompt-time defences and the measured injection results below are
  what the system actually relies on.
- Bare URLs in answers are not removed (a renderer may make them clickable; nothing is fetched).

## Measured prompt-injection resistance

A poisoned passage planted in the retrieved context (six attack styles, 20 questions) succeeded in 1.4% of
trials against the plain prompt path (5/354), and in 0 of 240 against the agent, which is the default
pipeline. Full method, numbers and caveats: [experiments.md](experiments.md#week-9-indirect-prompt-injection-runs-22-24).
An extra "spotlighting" prompt was tested and not adopted because its benefit is not statistically
distinguishable from noise (p = 0.22).
