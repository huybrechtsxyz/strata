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

Connection parameters

- Required:
  - `terraform` CLI available in `PATH`.

- Useful environment variables and parameters:
  - `TF_LOG` — logging level (TRACE, DEBUG, INFO, WARN, ERROR)
  - `TF_CLI_CONFIG_FILE` — custom CLI config file
  - `TF_DATA_DIR` — override default data directory
  - Backend configuration: provide backend values via `-backend-config` or `backend` block in Terraform configuration; xyz-platform can pass `backend_config` to `terraform init`.

Example — passing backend config through xyz-platform (integration call):

```python
# terraform.init(working_dir='/infra', backend_config={'bucket':'my-bucket','region':'eu'})
```

Usage
- `terraform init`
- `terraform plan`
- `terraform apply`

Docs
- https://www.terraform.io/docs
