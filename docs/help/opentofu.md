# OpenTofu Integration

OpenTofu is the Linux Foundation fork of Terraform, released under MPL-2.0.
It is a drop-in replacement with an identical CLI interface, state format, and provider
registry. Use `type: opentofu` to target the `tofu` binary instead of `terraform`.

Installation
- macOS: `brew install opentofu`
- Linux (script): `curl --proto '=https' --tlsv1.2 -fsSL https://get.opentofu.org/install-opentofu.sh | sh`
- Windows (Chocolatey): `choco install opentofu`
- Docs: https://opentofu.org/docs/intro/install/

Verify install
```
tofu --version
```

Minimum recommended version: 1.6.0

Configuration YAML

```yaml
integrations:
  - name: opentofu
    type: opentofu
    capabilities: [infrastructure]
    required: true
    validation:
      command: tofu --version
      min_version: "1.6.0"
```

Authentication
OpenTofu uses the same provider authentication as Terraform:

| Provider  | Setup                                                                                         |
| --------- | --------------------------------------------------------------------------------------------- |
| **Azure** | `az login` or `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID` / `ARM_SUBSCRIPTION_ID` |
| **AWS**   | `aws configure` or `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION`       |
| **GCP**   | `gcloud auth application-default login` or `GOOGLE_CREDENTIALS`                               |

Remote state backends
OpenTofu supports the same state backends as Terraform (S3, GCS, Azure Blob, Terraform Cloud).

For Terraform Cloud / HCP Terraform backend:

| Variable              | Purpose                      | Required                        |
| --------------------- | ---------------------------- | ------------------------------- |
| `TERRAFORM_API_TOKEN` | API token for remote runs    | Yes (if using TF Cloud backend) |
| `TF_VAR_*`            | Terraform variable overrides | No                              |
| `TF_CLI_ARGS`         | Extra CLI arguments          | No                              |

strata writes a temporary `.terraformrc` (or `terraform.rc` on Windows) with the API
token before running `tofu` commands, then removes it on completion.

For Terraform Cloud authentication details, see the `terraform-cloud-auth` help topic:
```
strata help terraform-cloud-auth
```

OpenTofu vs Terraform
OpenTofu and Terraform share the same CLI commands, provider ecosystem, and state format.
The binary name differs (`tofu` vs `terraform`). All strata workspace YAML fields work
identically for both — only the `type:` field in the integration declaration differs.

Common checks
```
tofu version
tofu init
tofu plan
tofu validate
```

Docs
- https://opentofu.org/docs
