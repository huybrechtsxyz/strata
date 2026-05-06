# =============================================================================
# XYZ Platform - Outputs
# =============================================================================

output "deployment_info" {
  description = "Deployment information"
  value = {
    workspace_name    = var.workspace_name
    workspace_version = var.workspace_version
    deployment_name   = var.deployment_name
    environment       = var.environment
    platform_version  = var.platform_version
    deployment_id     = local.deployment_id
  }
}

output "providers" {
  description = "Configured providers"
  value       = var.platform_providers
}

output "topologies" {
  description = "Infrastructure topologies"
  value       = var.topologies
}

output "resource_count" {
  description = "Number of resources configured"
  value       = length(var.resources)
}

output "deployment_status" {
  description = "Deployment status message"
  value       = "XYZ Platform deployment '${var.deployment_name}' initialized successfully"
}
