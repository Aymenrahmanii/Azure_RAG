# Learning log

Problems hit and how they were solved. Interview preparation.

| Date | Problem | Cause | Fix |
|---|---|---|---|
| 2026-10-03 | Chunker found 0 articles in 3 of 4 regulations | EU texts use non-breaking spaces ( ) in headings | Normalise whitespace with `" ".join(line.split())`; validate counts against the official article numbers |
| 2026-10-03 | EUR-Lex returned HTTP 202 and no body | Bot challenge on scripted requests | Use the Publications Office API (`publications.europa.eu/resource/celex/...`) with content negotiation |
| 2026-10-03 | Eval crashed with HTTP 400 on the prompt-injection question | Azure content filter (prompt shield) blocks the request, even when the *judge* prompt quotes the attack | Typed `ContentFiltered` exception; blocked = refusal; judge fallback to phrase match |
| 2026-10-03 | GPT-5-family models reject `temperature=0` | Reasoning models only allow the default | Make temperature optional |
| 2026-10-03 | `terraform plan` 401 on provider registration | Terraform shells out to `az`, which was not on PATH; also auto-registration needs broad rights | Fix PATH; `resource_provider_registrations = "none"` and register `Microsoft.App` once |
| 2026-10-03 | `terraform apply` 401 `RequestDisallowedByAzure` | Tenant requires MFA for writes; token had `amr: pwd` only (reads worked, writes did not) | Re-login via device code in a private window; verified `amr: pwd,mfa` by decoding the JWT before retrying |
| 2026-10-03 | AI Search rejected document keys like `gdpr:Article_5:0` | Keys may only contain letters, digits, `_`, `-`, `=` | Sanitise the key, keep the original id in a `chunk_id` field |
| 2026-10-03 | Search reported 1093 docs after ingesting 1337 | `get_document_count` is eventually consistent | Verify by enumerating (`search *`) instead of trusting the counter |
