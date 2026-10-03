terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Local state for now (git-ignored). Week 8 moves this to a remote backend in Blob Storage.
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = true # so destroy/apply cycles can reuse the name
    }
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id
}
