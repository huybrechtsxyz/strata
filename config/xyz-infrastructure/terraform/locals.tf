# =============================================================================
# XYZ Platform - Local Values
# =============================================================================

locals {
  # Common tags applied to all resources
  common_tags = merge(
    var.labels,
    {
      workspace   = var.workspace_name
      deployment  = var.deployment_name
      environment = var.environment
      managed_by  = "terraform"
      platform    = "xyz-platform"
    }
  )

  # Deployment identifier
  deployment_id = "${var.workspace_name}-${var.environment}"
}
