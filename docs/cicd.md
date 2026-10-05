# CI/CD

```
pull request ──► CI (lint, format, tests, terraform validate, image builds)
             └─► Eval gate
                    tier 1: retrieval-only eval  ─► gate   (deterministic, ~free)
                    tier 2: agent eval + judge   ─► gate   (60 questions, a few cents)

push to main ──► Eval gate ─► build + push images (tag = commit) ─► [manual approval]
                 ─► roll out ─► wait for new revision ─► smoke test ─► (rollback on failure)
```

## Why it is built this way

- **The gate compares metrics with floors in [eval/thresholds.json](../eval/thresholds.json)**, set a little
  below the measured runs so judge noise (about +-0.03 overall) does not fail good changes. A change that
  lowers quality fails the check; `python -m eval.gate` was checked against the weaker pipelines
  (baseline, global-only) and fails on them, and it refuses runs with fewer than 50 questions.
- **Two tiers.** Retrieval-only results are deterministic and cost almost nothing, so they catch chunking,
  index and retrieval regressions first. The agent run with the LLM judge is the real quality check.
- **No stored cloud credentials.** GitHub issues a short-lived OIDC token; Azure exchanges it for a
  user-assigned managed identity (`id-azrag-dev-ci`, [infra/ci.tf](../infra/ci.tf)). Only three token
  subjects are trusted: pushes to `main`, pull requests from this repo, and the `production` environment.
  A user-assigned identity with federated credentials needs no Entra app registration.
- **Least privilege.** The identity can read the search index, call Azure OpenAI, read the graph blob, push
  images, and is Contributor on the two Container Apps only. It cannot change infrastructure.
- **Terraform owns infrastructure, CI owns the running image.** After the first deploy the image is under
  `ignore_changes`, so `terraform apply` never rolls back a CI deployment. Terraform state is local, so CI
  does not run `apply`; infrastructure changes are applied by hand and only validated in CI.
- **Rollback.** The deploy job records the images running before the roll-out and restores them if the new
  revision never becomes ready or the smoke test (health check plus a real question that must mention
  "72 hours") fails. Container Apps in single-revision mode also keeps the old revision serving until the
  new one passes its health probes.

## One-time setup in GitHub (Settings)

1. **Secrets and variables > Actions > Variables** (not secrets; none of these grant access by themselves):

   | Variable | Value |
   |---|---|
   | `AZURE_CLIENT_ID` | `terraform output ci_client_id` |
   | `AZURE_TENANT_ID` | `terraform output tenant_id` |
   | `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
   | `AZURE_OPENAI_ENDPOINT` | the `AZURE_OPENAI_ENDPOINT` from your `.env` |
   | `AZURE_SEARCH_ENDPOINT` | `terraform output search_endpoint` |
   | `STORAGE_ACCOUNT` | `terraform output storage_account` |

   Until `AZURE_CLIENT_ID` exists, the Azure-dependent jobs are skipped instead of failing.
2. **Environments > New environment `production`**, and add yourself under *Required reviewers*. That is the
   manual approval before every deployment.
3. **Branches > Add rule for `main`**: require the checks `test`, `terraform`, `images` and `eval`, and
   require a pull request. (The eval job is skipped, which counts as passing, for fork PRs and until the
   variables exist. A workflow skipped by a `paths:` filter would stay pending, so there is no filter.)

## Updating the thresholds

Raise them after a verified improvement. Lower them only with a written reason in
[experiments.md](experiments.md), because that is the moment quality is being traded away.

## Not done yet

- Canary traffic splitting (multiple-revision mode with a weight shift) instead of the single-revision
  rollout; the single-revision mode with health probes and automatic rollback was chosen for simplicity.
- A separate dev and prod environment: there is one Azure environment, and "production" is the approval gate.
- Pinning third-party actions to commit SHAs (they use major-version tags).

## Optional: authenticated smoke test

The API requires a token, so the deploy smoke test always checks that a request without one is rejected (401).
To also check a real answer after each deploy, mint a token for a user with no groups and store it as the
repository **secret** `SMOKE_TOKEN`:

```
python -m app.security.mint mint --key .keys/private.pem --issuer https://azrag.dev \
    --audience azrag-api --sub ci-smoke --groups "" --ttl-hours 720
```

It expires after 30 days (then the step fails loudly, which is the reminder to mint a new one). The private
key itself must never go into GitHub.

## Verified in real Actions (2026-10-05)

One pull request and one merge ran the whole path: CI jobs, eval gate (PR and `main`), OIDC login with all three federated credentials, approval in the `production` environment, image build tagged with the commit SHA, and a healthy new Container Apps revision. Three first-run problems were fixed on the way (see the learning log): GitHub's ID-based OIDC subject, a carriage return in a variable, and the read-only identity trying to manage the search index. Automatic rollback has never been triggered, so it is untested.
