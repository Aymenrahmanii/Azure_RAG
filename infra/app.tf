# Week 6: the API on Container Apps (consumption plan, scale to zero).
# Two-step first deploy, because the app needs an image that must already be in the registry:
#   1. terraform apply -target=azurerm_container_registry.main
#   2. az acr build -r <acr> -t azrag-api:<tag> .      (builds in the cloud, no local Docker needed)
#   3. terraform apply -var api_image_tag=<tag>

variable "api_image_tag" {
  description = "Tag of azrag-api in the registry. Empty = do not create the Container App yet."
  type        = string
  default     = ""
}

variable "ui_image_tag" {
  description = "Tag of azrag-ui in the registry. Empty = do not create the UI Container App."
  type        = string
  default     = ""
}

variable "api_max_replicas" {
  description = "Upper bound on replicas: a cost and Azure OpenAI quota safety net."
  type        = number
  default     = 3
}

# ---- API security (docs/security.md) ------------------------------------------------------------
# The tenant forbids app registrations, so tokens are self-issued (python -m app.security.mint) and
# the API only holds the PUBLIC key. Moving to Entra ID later means AUTH_JWKS_URL instead of a key.

variable "auth_issuer" {
  type    = string
  default = "https://azrag.dev"
}

variable "auth_audience" {
  type    = string
  default = "azrag-api"
}

variable "auth_public_key" {
  description = "PEM public key that verifies access tokens (not a secret). Set in a git-ignored tfvars file."
  type        = string
  default     = ""
}

variable "acl_restricted" {
  description = "JSON map of source -> groups allowed to read it, e.g. {\"dora\":[\"finance\"]}. Empty = all sources open to any authenticated user."
  type        = string
  default     = ""
}

variable "rate_limit_per_minute" {
  type    = number
  default = 20
}

# Salts the hashes in the audit log. Lives in Terraform state and a Container Apps secret only.
resource "random_password" "audit_salt" {
  length  = 32
  special = false
}

resource "azurerm_container_registry" "main" {
  name                = "acr${var.project}${var.environment}${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic" # about 5 USD/month: the only always-on cost of this stage
  admin_enabled       = false   # no shared password; the app pulls with its managed identity
  tags                = local.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_container_app" "api" {
  count                        = var.api_image_tag == "" ? 0 : 1
  name                         = "ca-${local.name}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption" # the environment's default profile; pinned to avoid drift
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name  = "audit-salt"
    value = random_password.audit_salt.result
  }

  secret {
    name  = "appinsights-connection-string"
    value = azurerm_application_insights.main.connection_string
  }

  ingress {
    external_enabled = true
    transport        = "http"
    target_port      = 8000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0 # scale to zero: no cost while idle, cold start of a few seconds
    max_replicas = var.api_max_replicas

    http_scale_rule {
      name                = "http-concurrency"
      concurrent_requests = "10"
    }

    container {
      name   = "api"
      image  = "${azurerm_container_registry.main.login_server}/azrag-api:${var.api_image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "AZURE_CLIENT_ID" # makes DefaultAzureCredential use the user-assigned identity
        value = azurerm_user_assigned_identity.app.client_id
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "VECTOR_STORE"
        value = "azure_search"
      }
      env {
        name  = "AZURE_SEARCH_ENDPOINT"
        value = "https://${azurerm_search_service.main.name}.search.windows.net"
      }
      env {
        name  = "EMBEDDING_MODEL"
        value = "azure-openai:${azurerm_cognitive_deployment.embedding.name}"
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = trimsuffix(data.azurerm_cognitive_account.openai.endpoint, "/")
      }
      env {
        name  = "LLM_AUTH"
        value = "entra"
      }
      env {
        name  = "LLM_BASE_URL"
        value = "${trimsuffix(data.azurerm_cognitive_account.openai.endpoint, "/")}/openai/v1"
      }
      env {
        name  = "LLM_MODEL"
        value = var.chat_deployment
      }
      env {
        name  = "STORAGE_ACCOUNT_URL"
        value = azurerm_storage_account.main.primary_blob_endpoint
      }
      env {
        name  = "GRAPH_CONTAINER"
        value = azurerm_storage_container.graph.name
      }
      env {
        name  = "AUTH_MODE"
        value = "jwt"
      }
      env {
        name  = "AUTH_ISSUER"
        value = var.auth_issuer
      }
      env {
        name  = "AUTH_AUDIENCE"
        value = var.auth_audience
      }
      env {
        name  = "AUTH_PUBLIC_KEY"
        value = replace(var.auth_public_key, "\n", "\\n") # one line; the API restores the newlines
      }
      env {
        name  = "ACL_RESTRICTED"
        value = var.acl_restricted
      }
      env {
        name  = "RATE_LIMIT_PER_MINUTE"
        value = tostring(var.rate_limit_per_minute)
      }
      env {
        name        = "AUDIT_SALT"
        secret_name = "audit-salt"
      }
      env {
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection-string"
      }
      env {
        name  = "OTEL_PYTHON_EXCLUDED_URLS" # liveness/startup probes would otherwise be ~70% of requests
        value = "healthz"
      }

      startup_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8000
        # The BM25 index is built at startup from every chunk in AI Search.
        failure_count_threshold = 20
        interval_seconds        = 5
      }
      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8000
      }
    }
  }

  depends_on = [azurerm_role_assignment.app_acr_pull, azurerm_role_assignment.this]

  lifecycle {
    # CI/CD rolls out new images (az containerapp update). Terraform owns everything else.
    ignore_changes = [template[0].container[0].image]

    precondition {
      condition     = var.auth_public_key != ""
      error_message = "auth_public_key is required: the API refuses to start without authentication."
    }
  }
}

variable "chat_deployment" {
  description = "Name of the chat model deployment on the existing Azure OpenAI resource."
  type        = string
  default     = "gpt-5.4-mini"
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "api_url" {
  value = try("https://${azurerm_container_app.api[0].ingress[0].fqdn}", null)
}

resource "azurerm_container_app" "ui" {
  count                        = var.ui_image_tag == "" || var.api_image_tag == "" ? 0 : 1
  name                         = "ca-${local.name}-ui"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption" # the environment's default profile; pinned to avoid drift
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true
    transport        = "http"
    target_port      = 8501
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1 # Streamlit keeps per-session state in the process: one replica is enough here

    container {
      name   = "ui"
      image  = "${azurerm_container_registry.main.login_server}/azrag-ui:${var.ui_image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "API_URL"
        value = "https://${azurerm_container_app.api[0].ingress[0].fqdn}"
      }
    }
  }

  depends_on = [azurerm_role_assignment.app_acr_pull]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

output "ui_url" {
  value = try("https://${azurerm_container_app.ui[0].ingress[0].fqdn}", null)
}

# ---- Knowledge graph artifact: built offline (python -m app.graph.build), loaded by the API ------

resource "azurerm_storage_container" "graph" {
  name                  = "graph"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}
