variable "subscription_id" {
  description = "Azure subscription id (set in terraform.tfvars, which is git-ignored)."
  type        = string
}

variable "location" {
  description = "Azure region for new resources."
  type        = string
  default     = "westeurope"
}

variable "project" {
  type    = string
  default = "azrag"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "openai_account_name" {
  description = "Name of the EXISTING Foundry / Azure OpenAI resource (created in the portal)."
  type        = string
}

variable "openai_resource_group" {
  description = "Resource group of the existing Foundry / Azure OpenAI resource."
  type        = string
}

variable "search_sku" {
  description = "AI Search tier. 'free' costs nothing; 'basic' is roughly 75 USD/month."
  type        = string
  default     = "free"

  validation {
    condition     = contains(["free", "basic"], var.search_sku)
    error_message = "Use 'free' or 'basic' (anything bigger is out of budget)."
  }
}

variable "log_daily_cap_gb" {
  description = "Daily ingestion cap for Log Analytics, a cost safety net."
  type        = number
  default     = 0.5
}
