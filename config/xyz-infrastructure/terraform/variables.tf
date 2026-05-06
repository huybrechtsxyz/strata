# =============================================================================
# XYZ Platform - Input Variables
# =============================================================================

# Workspace

variable "workspace_name" {
  description = "Name of the workspace"
  type        = string
}

variable "workspace_version" {
  description = "Version of the workspace"
  type        = string
}

# Deployment

variable "deployment_name" {
  description = "Name of the deployment"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, tst, prd)"
  type        = string
}

variable "platform_version" {
  description = "Platform version"
  type        = string
}

variable "labels" {
  description = "Workspace labels"
  type        = map(string)
  default     = {}
}

variable "metadata" {
  description = "Additional workspace metadata"
  type        = any
  default     = {}
}

# Infrastructure

variable "platform_providers" {
  description = "Provider configurations"
  type        = map(any)
  default     = {}
}

variable "topologies" {
  description = "Topology configurations"
  type        = map(any)
  default     = {}
}

variable "resources" {
  description = "Resource configurations"
  type        = map(any)
  default     = {}
}

variable "modules" {
  description = "Module configurations"
  type        = map(any)
  default     = {}
}
