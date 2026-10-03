# Infrastructure (Terraform)

Creates `rg-azrag-dev` with: storage (+ `documents` container), Key Vault (RBAC), AI Search (free tier),
Log Analytics (0.5 GB/day cap), Application Insights, Container Apps environment (consumption), a user-assigned
managed identity, and least-privilege role assignments for that identity and for the developer.
The Foundry / Azure OpenAI resource already exists and is only referenced.

## One-time: register resource providers

Terraform is configured not to register providers itself (it needs broad rights and failed on a student
subscription). Register what is needed once: `az provider register -n Microsoft.App --wait`
(others used here: Storage, KeyVault, Search, OperationalInsights, Insights, ManagedIdentity, Authorization,
CognitiveServices; usually already registered).

## Use

```powershell
az login
az account show --query id -o tsv           # subscription id
cp example.tfvars terraform.tfvars          # fill in (git-ignored)
terraform init
terraform plan -out plan.tfplan             # read it: nothing here should be expensive
terraform apply plan.tfplan
```

Then set in `.env`: `VECTOR_STORE=azure_search`, `AZURE_SEARCH_ENDPOINT=<search_endpoint output>`,
`LLM_AUTH=entra` and clear `LLM_API_KEY`.

## Cost control

- Only `search_sku = "free"` is intended. Basic is ~75 USD/month: it would eat most of a 100 USD credit in weeks.
- Tear everything down when not working: `terraform destroy` (recreate with `apply`; re-run `python -m app.cli ingest`).
- Never commit `terraform.tfvars` or `*.tfstate` (both are git-ignored).
