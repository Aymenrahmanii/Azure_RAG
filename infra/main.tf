data "azurerm_client_config" "current" {}

# Existing Foundry / Azure OpenAI resource: referenced, not managed (brownfield).
data "azurerm_cognitive_account" "openai" {
  name                = var.openai_account_name
  resource_group_name = var.openai_resource_group
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  name   = "${var.project}-${var.environment}"
  suffix = random_string.suffix.result
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# Identity the application runs as. No keys anywhere: access is granted by role assignments below.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

# ---- Observability ----------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = var.log_daily_cap_gb
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

# ---- Storage (source documents) ---------------------------------------------------------------

resource "azurerm_storage_account" "main" {
  name                            = "st${var.project}${var.environment}${local.suffix}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "documents" {
  name                  = "documents"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

# ---- Key Vault (RBAC mode: no access policies) ------------------------------------------------

resource "azurerm_key_vault" "main" {
  name                       = "kv-${var.project}-${local.suffix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false # dev only; enable in prod
  tags                       = local.tags
}

# ---- AI Search --------------------------------------------------------------------------------

resource "azurerm_search_service" "main" {
  name                = "srch-${local.name}-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.search_sku
  # Accept both API keys and Entra tokens. The app only uses Entra (role assignments below);
  # keys stay as a break-glass option. Disable them once everything works keyless.
  local_authentication_enabled = true
  authentication_failure_mode  = "http403"
  tags                         = local.tags
}

# ---- Container Apps environment (consumption: no cost while no app runs) -----------------------

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags

  lifecycle {
    # Azure adds a default "Consumption" workload profile that Terraform cannot remove.
    ignore_changes = [workload_profile]
  }
}

# ---- Role assignments: least privilege, for the app identity AND the developer (az login) ------

locals {
  principals = {
    app = azurerm_user_assigned_identity.app.principal_id
    dev = data.azurerm_client_config.current.object_id
  }

  roles = {
    openai_user     = { scope = data.azurerm_cognitive_account.openai.id, role = "Cognitive Services OpenAI User" }
    search_data     = { scope = azurerm_search_service.main.id, role = "Search Index Data Contributor" }
    search_manage   = { scope = azurerm_search_service.main.id, role = "Search Service Contributor" }
    blob_data       = { scope = azurerm_storage_account.main.id, role = "Storage Blob Data Contributor" }
    keyvault_secret = { scope = azurerm_key_vault.main.id, role = "Key Vault Secrets User" }
  }

  assignments = {
    for pair in setproduct(keys(local.principals), keys(local.roles)) :
    "${pair[0]}-${pair[1]}" => { principal = local.principals[pair[0]], role = local.roles[pair[1]] }
  }
}

resource "azurerm_role_assignment" "this" {
  for_each             = local.assignments
  scope                = each.value.role.scope
  role_definition_name = each.value.role.role
  principal_id         = each.value.principal
}

