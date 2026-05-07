# Terraform Infrastructure

This project provisions servers and networking resources using Terraform with among others Kamatera and stores the back-end in HCP Cloud.

## Required Variables

Some variables must be exported as environment variables (TF_VAR_*) or provided via .tfvars files.

### For HCP Cloud

- TF_VAR_organization
- TF_VAR_cloudspace

### For All Environments

- workspace (used to select the configuration in workspace.tfvars)

### For Kamatera

- TF_VAR_kamatera_api_key
- TF_VAR_kamatera_api_secret
- TF_VAR_kamatera_root_password
- TF_VAR_kamatera_public_key

## Workspace Configuration

Each workspace is defined in a workspace.tfvars file. This file is generated or overwritten by the CI/CD pipeline during terraform apply.

Terraform creates the resources; servers are created based on the virtualmachines map in workspace.tfvars. Networking resources are created to support those servers.

Example workspace.tfvars:

```hcl
# Kamatera Datacenter Variables
kamatera_country = "Germany"
kamatera_region  = "Frankfurt"

# Kamatera Server Variables
kamatera_manager_id = "manager-1"

# Server configuration
virtualmachines = {
  "vm-manager" = {
    provider  = "kamatera"
    role      = "manager"
    count     = 1
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 1024
    disks_gb  = [20]       # root only
    billing   = "hourly"
    unit_cost = 5.00
  }
  "vm-infrastructure" = {
    provider  = "kamatera"
    role      = "infra"
    count     = 2
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 4096
    disks_gb  = [20, 40]   # root + block
    billing   = "hourly"
    unit_cost = 11.00
  }
  "vm-workers" = {
    provider  = "kamatera"
    role      = "worker"
    count     = 2
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 2048
    disks_gb  = [20]
    billing   = "hourly"
    unit_cost = 6.00
  }
}
```