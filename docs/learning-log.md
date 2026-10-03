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
| 2026-10-03 | Function host crashed at startup: 403 `AuthenticationFailed` (MAC signature) on `azure-webjobs-secrets` | azurerm injects an `AzureWebJobsStorage` connection string with an EMPTY AccountKey even when using managed identity; the host signs requests with it | Delete that setting and use explicit `AzureWebJobsStorage__blob/queue/tableServiceUri` + `__credential=managedidentity`; codified in Terraform. (I changed two things at once, so I cannot say which alone fixed it) |
| 2026-10-03 | Event Grid subscription creation failed: `Webhook endpoint validation failed ... NotFound` | The function host was not running (above), so Event Grid could not validate the endpoint | Fix the host first; keep the subscription behind `enable_event_subscription` because it can only exist after the code is deployed |
| 2026-10-03 | Thousands of "Request URL ... Request headers" log lines | Azure SDKs log every HTTP call at INFO; Log Analytics bills by ingestion | `logging.getLogger("azure").setLevel(WARNING)` in the function app |
| 2026-10-03 | `az functionapp ... config-zip` printed "Failed to fetch host key" | Post-deploy health check cannot read keys while the host is unhealthy | Ignore it; verify with `az functionapp function list` and App Insights traces instead |
| 2026-10-03 | Embedding 1,337 chunks took ~10 minutes | 50K tokens/min quota on the deployment; many 429s | Retry with `Retry-After` backoff in the client (already there); raise capacity if ingest speed matters |
| 2026-10-03 | Terraform kept proposing to delete the Container Apps "Consumption" workload profile | Azure adds a default profile Terraform cannot remove | `lifecycle { ignore_changes = [workload_profile] }` |
