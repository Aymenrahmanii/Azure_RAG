# 007: Eval thresholds gate the build

**Status:** accepted (2026-10-04)

**Decision.** Pull requests run lint, tests, Terraform validation, image builds and an eval gate
(`eval/gate.py`, thresholds in `eval/thresholds.json`); `main` deploys behind a manual approval with
rollback, authenticating to Azure by OIDC (no stored secrets).

**Consequences.** Quality regressions block merges, but the judge is the same model as the generator
(optimistic) and individual scores are noisy (0.3-0.5 swings per question), so thresholds must leave
margin. The eval-gate and deploy workflows are not verified in real GitHub Actions until the one-time
repository setup in [cicd.md](../cicd.md) is done.
