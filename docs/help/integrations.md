# Integrations

External tools that extend strata platform capabilities.

Integrations are declared under `spec.integrations` in `configuration.yaml`.
Each integration connects a strata workspace to an external system: a provisioner
(Terraform, Ansible), a secret store (Key Vault, Bitwarden), an AI provider
(OpenAI, Azure OpenAI), or an audit sink (Sentinel, Splunk).

---

## Integration Types

| Type                                                                                      | Purpose                                        | Requires auth?                    |
| ----------------------------------------------------------------------------------------- | ---------------------------------------------- | --------------------------------- |
| `terraform`                                                                               | IaC provisioner                                | Yes (cloud CLI or API key)        |
| `ansible`                                                                                 | Config management                              | Yes (SSH key or password)         |
| `docker`                                                                                  | Container runtime                              | Sometimes (Docker daemon)         |
| `helm`                                                                                    | Kubernetes package manager                     | Sometimes (kubeconfig)            |
| `ai_agent`                                                                                | LLM for advisory analysis                      | Yes (API key or azure_cli)        |
| `azure_keyvault`                                                                          | Secret store (Azure)                           | Yes (azure_cli)                   |
| `aws_secretsmanager`                                                                      | Secret store (AWS)                             | Yes (aws_cli)                     |
| `bitwarden`                                                                               | Secret store (self-hosted)                     | Yes (API token)                   |
| `hashicorp_vault`                                                                         | Secret store (self-hosted)                     | Yes (JWT or token)                |
| `sentinel`                                                                                | SIEM (audit sink)                              | Yes (API key or managed identity) |
| `splunk`                                                                                  | SIEM (audit sink)                              | Yes (API token)                   |
| `elk`                                                                                     | SIEM (audit sink)                              | Yes (HTTP auth)                   |
| `checkov`                                                                                 | Policy-as-code scanner                         | No                                |
| `opa`                                                                                     | Open Policy Agent                              | Yes (API if remote)               |
| `infracost`                                                                               | Cost estimation                                | Yes (API key)                     |
| `azure_ad` / `google` / `aws_identity_center` / `auth0` / `github_oauth` / `generic_oidc` | Identity provider (control-plane / OIDC login) | Yes (OIDC/OAuth2 device flow)     |

---

## Basic Integration Example

```yaml
spec:
  integrations:
    - name: terraform
      type: terraform
      properties:
        backend: cloud
      authentication:
        method: terraform_cloud
        api_key: TF_API_TOKEN

    - name: ai-advisor
      type: ai_agent
      endpoints:
        address: https://my-aoai.openai.azure.com/
      authentication:
        method: azure_cli
      properties:
        provider: azure_openai
        model: gpt-4o
        enabled_hooks: [deploy_plan_after]
```

---

## Authentication Methods

| Method         | Use case                                             |
| -------------- | ---------------------------------------------------- |
| `none`         | Tool already configured (docker, local ollama)       |
| `env_var`      | Store credential in environment variable             |
| `api_key`      | Explicit API key (not recommended for prod)          |
| `azure_cli`    | Use `az login` identity (short-lived, no key stored) |
| `aws_cli`      | Use `aws sts` identity (short-lived)                 |
| `gcloud`       | Use `gcloud auth` identity (short-lived)             |
| `secret_store` | Look up credential in Key Vault / Bitwarden / Vault  |

---

## Discovery

- `strata help --topic ai_agent` — LLM configuration details
- `strata help --topic terraform` — Terraform provisioner
- `strata help --topic ansible` — Ansible provisioner
- `strata help --topic azure_keyvault` — Azure secret store
- `strata help --topic identity` — CLI login to a control plane or any OIDC service
- `strata help --topic bitwarden` — Bitwarden secret store
