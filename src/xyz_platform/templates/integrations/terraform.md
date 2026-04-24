# Terraform Integration

Installation
- Download Terraform from https://www.terraform.io/downloads or use package managers:
  - macOS: `brew install terraform`
  - Linux: use package manager or download release

Configuration
- Ensure `terraform` is in `PATH` and verify with `terraform version`.
- Initialize providers: `terraform init` in your configuration directory.

Environment Variables
- `TF_LOG`: logging level (TRACE, DEBUG, INFO, WARN, ERROR)
- `TF_CLI_CONFIG_FILE`: custom CLI config file
- `TF_DATA_DIR`: override default data directory

Usage
- `terraform init`
- `terraform plan`
- `terraform apply`

Docs
- https://www.terraform.io/docs
