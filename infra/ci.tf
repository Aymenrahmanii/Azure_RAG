# Week 8: CI/CD identity. GitHub Actions authenticates with OIDC (no stored secrets): GitHub issues a
# short-lived token for a specific repo + ref, and Azure exchanges it for this managed identity.
# A user-assigned managed identity with federated credentials needs no Entra app registration.

variable "github_repo" {
  description = "owner/name of the GitHub repository allowed to deploy."
  type        = string
  default     = "Aymenrahmanii/Azure_RAG"
}

resource "azurerm_user_assigned_identity" "ci" {
  name                = "id-${local.name}-ci"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

locals {
  # The token's `sub` claim must match exactly: that is what limits who can become this identity.
  ci_subjects = {
    main         = "repo:${var.github_repo}:ref:refs/heads/main"
    pull_request = "repo:${var.github_repo}:pull_request"
    production   = "repo:${var.github_repo}:environment:production" # deploy job, behind manual approval
  }
}

resource "azurerm_federated_identity_credential" "ci" {
  for_each            = local.ci_subjects
  name                = "github-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.ci.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = each.value
}

locals {
  ci_roles = {
    search_read = { scope = azurerm_search_service.main.id, role = "Search Index Data Reader" }
    openai_user = { scope = data.azurerm_cognitive_account.openai.id, role = "Cognitive Services OpenAI User" }
    graph_read  = { scope = azurerm_storage_account.main.id, role = "Storage Blob Data Reader" }
    acr_push    = { scope = azurerm_container_registry.main.id, role = "AcrPush" }
  }
}

resource "azurerm_role_assignment" "ci" {
  for_each             = local.ci_roles
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
}

# Rolling out a new image means updating the Container Apps, so scope Contributor to just those.
resource "azurerm_role_assignment" "ci_deploy_api" {
  count                = var.api_image_tag == "" ? 0 : 1
  scope                = azurerm_container_app.api[0].id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
}

resource "azurerm_role_assignment" "ci_deploy_ui" {
  count                = var.ui_image_tag == "" || var.api_image_tag == "" ? 0 : 1
  scope                = azurerm_container_app.ui[0].id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
}

output "ci_client_id" {
  value = azurerm_user_assigned_identity.ci.client_id
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}
