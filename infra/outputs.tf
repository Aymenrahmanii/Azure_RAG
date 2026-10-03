output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "search_endpoint" {
  value = "https://${azurerm_search_service.main.name}.search.windows.net"
}

output "openai_endpoint" {
  value = data.azurerm_cognitive_account.openai.endpoint
}

output "storage_account" {
  value = azurerm_storage_account.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "app_identity_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

output "container_app_environment" {
  value = azurerm_container_app_environment.main.name
}

output "app_insights_connection_string" {
  value     = azurerm_application_insights.main.connection_string
  sensitive = true
}
