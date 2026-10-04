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
}

output "ui_url" {
  value = try("https://${azurerm_container_app.ui[0].ingress[0].fqdn}", null)
}
