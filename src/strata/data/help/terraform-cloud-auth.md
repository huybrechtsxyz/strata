# Terraform Cloud Authentication

## The Problem

When running Terraform operations that use Terraform Cloud as a backend, you may see errors like:

```
app.terraform.io
token is not found
Run: terraform login
```

This means Terraform cannot authenticate with Terraform Cloud to access your
organisation's workspaces and state files.

## Authentication Methods

### Method 1: Environment Variable (recommended for CI/CD)

Set `TERRAFORM_API_TOKEN` before running any strata build or deploy command.

**PowerShell:**
```powershell
$env:TERRAFORM_API_TOKEN = "your-api-token-here"
```

**Command Prompt:**
```cmd
set TERRAFORM_API_TOKEN=your-api-token-here
```

**Bash / macOS / Linux:**
```bash
export TERRAFORM_API_TOKEN="your-api-token-here"
```

For a persistent setup add the export to your shell profile
(`$PROFILE` on PowerShell, `~/.bashrc` or `~/.zshrc` on Unix).

### Method 2: Terraform CLI Login (recommended for local development)

```bash
terraform login
```

This opens a browser to Terraform Cloud, generates an API token, and saves it
to the Terraform credentials file automatically.

### Method 3: Manual Credentials File

Create or edit:

- **Windows:** `%APPDATA%\terraform.d\credentials.tfrc.json`
- **Linux / macOS:** `~/.terraform.d/credentials.tfrc.json`

```json
{
  "credentials": {
    "app.terraform.io": {
      "token": "your-api-token-here"
    }
  }
}
```

## Getting an API Token

1. Go to https://app.terraform.io/app/settings/tokens
2. Click **Create an API token**
3. Enter a description (e.g. `strata dev`)
4. Copy the generated token
5. Use it with one of the methods above

## CI/CD Integration

**GitHub Actions:**
```yaml
env:
  TERRAFORM_API_TOKEN: ${{ secrets.TERRAFORM_API_TOKEN }}
```

**Azure DevOps:**
```yaml
variables:
  TERRAFORM_API_TOKEN: $(terraform-api-token)
```

**Jenkins:**
```groovy
environment {
    TERRAFORM_API_TOKEN = credentials('terraform-api-token')
}
```

## Troubleshooting

### "Terraform binary not found in PATH"

Install Terraform CLI and add it to your system PATH.
Download from https://www.terraform.io/downloads

### "authentication not configured"

Use one of the three authentication methods above.

### "terraform login failed"

Likely causes: no browser available (headless environment), network issue, or
Terraform not installed.
Use the API token environment variable method instead.

### "duplicate provider configurations"

Caused by leftover `.template.tf` files after processing.
Ensure `cleanup_templates=True` in your provisioner configuration so template
files are removed after rendering.

## Security Best Practices

1. Never commit API tokens to version control.
2. Use environment variables or a secrets manager (Azure Key Vault, Bitwarden, HashiCorp Vault) rather than credentials files in shared environments.
3. Rotate tokens regularly — recommended every 90 days.
4. Grant tokens the minimum required scope.
5. Monitor token usage in Terraform Cloud audit logs.

## Related Topics

- `strata help terraform` — Terraform integration overview and environment variables
- `strata help azure_keyvault` — Azure Key Vault secret resolution
- `strata help hashicorp_vault` — HashiCorp Vault secret resolution
- `strata help bitwarden` — Bitwarden Secrets Manager integration
- https://www.terraform.io/cloud-docs — Terraform Cloud documentation
- https://www.terraform.io/cli/commands/login — terraform login reference
