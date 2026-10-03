# Week 5: event-driven ingestion.
# Blob upload/delete -> Event Grid -> Function (enqueue) -> Service Bus queue (+ DLQ) -> Function (reconcile)
# Status per document lives in Cosmos DB (serverless). Everything authenticates with managed identity.

# ---- Embedding model on the existing Azure OpenAI / Foundry account ---------------------------

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = data.azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 50 # thousand tokens per minute; pay-per-token, so the cap is only a safety limit
  }
}

# ---- Cosmos DB (serverless): ingestion status per document -------------------------------------

resource "azurerm_cosmosdb_account" "main" {
  name                = "cosmos-${local.name}-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  tags                = local.tags

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }

  local_authentication_enabled = false # Entra only: no account keys exist to leak
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "ragdb"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "documents" {
  name                  = "documents"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/id"]
  partition_key_version = 2
}

# Built-in "Cosmos DB Built-in Data Contributor" data-plane role.
resource "azurerm_cosmosdb_sql_role_assignment" "this" {
  for_each            = local.principals
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = each.value
  scope               = azurerm_cosmosdb_account.main.id
}

# ---- Service Bus: ingestion queue with retries and a dead-letter queue --------------------------

resource "azurerm_servicebus_namespace" "main" {
  name                = "sb-${local.name}-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic" # about 0.05 USD per million operations
  local_auth_enabled  = false   # Entra only
  tags                = local.tags
}

resource "azurerm_servicebus_queue" "ingest" {
  name         = "ingest"
  namespace_id = azurerm_servicebus_namespace.main.id

  max_delivery_count                   = 5      # after 5 failed attempts the message goes to the DLQ
  lock_duration                        = "PT5M" # long enough to embed a large document
  default_message_ttl                  = "P1D"
  dead_lettering_on_message_expiration = true
}

# ---- Function app (Flex Consumption, Python) -----------------------------------------------------

resource "azurerm_storage_container" "func_packages" {
  name                  = "func-packages"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "func" {
  name                = "plan-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = local.tags
}

resource "azurerm_function_app_flex_consumption" "ingest" {
  name                = "func-${local.name}-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  service_plan_id     = azurerm_service_plan.func.id
  tags                = local.tags

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.main.primary_blob_endpoint}${azurerm_storage_container.func_packages.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.app.id

  runtime_name           = "python"
  runtime_version        = "3.11"
  maximum_instance_count = 40
  instance_memory_in_mb  = 2048

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
  }

  app_settings = {
    # Identity-based connections (no connection strings or keys)
    "AzureWebJobsStorage__accountName" = azurerm_storage_account.main.name
    "AzureWebJobsStorage__credential"  = "managedidentity"
    "AzureWebJobsStorage__clientId"    = azurerm_user_assigned_identity.app.client_id
    # Explicit service URIs: the host did not start with accountName alone (see learning-log).
    "AzureWebJobsStorage__blobServiceUri"  = trimsuffix(azurerm_storage_account.main.primary_blob_endpoint, "/")
    "AzureWebJobsStorage__queueServiceUri" = trimsuffix(azurerm_storage_account.main.primary_queue_endpoint, "/")
    "AzureWebJobsStorage__tableServiceUri" = trimsuffix(azurerm_storage_account.main.primary_table_endpoint, "/")

    "ServiceBusConnection__fullyQualifiedNamespace" = "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
    "ServiceBusConnection__credential"              = "managedidentity"
    "ServiceBusConnection__clientId"                = azurerm_user_assigned_identity.app.client_id

    # Picked up by DefaultAzureCredential so it uses the user-assigned identity
    "AZURE_CLIENT_ID" = azurerm_user_assigned_identity.app.client_id

    # Application settings read by the ingestion code
    "ENVIRONMENT"           = var.environment
    "VECTOR_STORE"          = "azure_search"
    "AZURE_SEARCH_ENDPOINT" = "https://${azurerm_search_service.main.name}.search.windows.net"
    "EMBEDDING_MODEL"       = "azure-openai:${azurerm_cognitive_deployment.embedding.name}"
    "AZURE_OPENAI_ENDPOINT" = trimsuffix(data.azurerm_cognitive_account.openai.endpoint, "/")
    "COSMOS_ENDPOINT"       = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_DATABASE"       = azurerm_cosmosdb_sql_database.main.name
    "COSMOS_CONTAINER"      = azurerm_cosmosdb_sql_container.documents.name
    "STORAGE_ACCOUNT_URL"   = azurerm_storage_account.main.primary_blob_endpoint
    "DOCUMENTS_CONTAINER"   = azurerm_storage_container.documents.name
    "INGEST_QUEUE"          = azurerm_servicebus_queue.ingest.name
  }
}

# Roles needed only by the app identity (host storage, queues, event processing).
locals {
  app_only_roles = {
    storage_owner = { scope = azurerm_storage_account.main.id, role = "Storage Blob Data Owner" }
    storage_queue = { scope = azurerm_storage_account.main.id, role = "Storage Queue Data Contributor" }
    storage_table = { scope = azurerm_storage_account.main.id, role = "Storage Table Data Contributor" }
    servicebus_rx = { scope = azurerm_servicebus_namespace.main.id, role = "Azure Service Bus Data Receiver" }
    servicebus_tx = { scope = azurerm_servicebus_namespace.main.id, role = "Azure Service Bus Data Sender" }
  }
}

resource "azurerm_role_assignment" "app_only" {
  for_each             = local.app_only_roles
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# The developer also sends test messages and reads the queue / DLQ.
resource "azurerm_role_assignment" "dev_servicebus" {
  for_each             = toset(["Azure Service Bus Data Owner"])
  scope                = azurerm_servicebus_namespace.main.id
  role_definition_name = each.value
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---- Event Grid: blob created / deleted -> function ----------------------------------------------
# Created in a second step, after the function code is deployed (Event Grid validates the endpoint).

variable "enable_event_subscription" {
  description = "Set true after deploying the function code."
  type        = bool
  default     = false
}

resource "azurerm_eventgrid_system_topic" "storage" {
  name                = "evgt-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  source_resource_id  = azurerm_storage_account.main.id
  topic_type          = "Microsoft.Storage.StorageAccounts"
  tags                = local.tags
}

resource "azurerm_eventgrid_system_topic_event_subscription" "blob_events" {
  count                 = var.enable_event_subscription ? 1 : 0
  name                  = "blob-to-ingest"
  system_topic          = azurerm_eventgrid_system_topic.storage.name
  resource_group_name   = azurerm_resource_group.main.name
  event_delivery_schema = "EventGridSchema"

  included_event_types = ["Microsoft.Storage.BlobCreated", "Microsoft.Storage.BlobDeleted"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/${azurerm_storage_container.documents.name}/"
  }

  azure_function_endpoint {
    function_id = "${azurerm_function_app_flex_consumption.ingest.id}/functions/on_blob_event"
  }

  retry_policy {
    max_delivery_attempts = 10
    event_time_to_live    = 1440
  }
}

