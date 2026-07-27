# Provider

Cloud provider credentials and region configuration.

A provider (`kind: provider`) defines:
- **Cloud credentials** — AWS account, Azure subscription, GCP project
- **Regions / locations** — where resources are deployed
- **Authentication** — how the provisioner authenticates
- **Defaults** — region, availability zone, resource tags

Providers are referenced by workspaces and resources to establish which cloud
account and region a resource belongs to.

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: provider
meta:
  name: aws-primary
spec:
  type: aws
  region: us-east-1
  credentials:
    method: iam_role    # or: access_key, assume_role, sso
    role_arn: arn:aws:iam::123456789012:role/TerraformRole
  tags:
    Environment: production
    Owner: platform-team
```

---

## Provider Types

| Type         | Credentials                                   | Use Case            |
| ------------ | --------------------------------------------- | ------------------- |
| `aws`        | IAM role, access keys, STS assume-role        | Amazon Web Services |
| `azure`      | Managed Identity, Service Principal, CLI auth | Azure (ARM)         |
| `gcp`        | Service account, gcloud CLI                   | Google Cloud        |
| `kubernetes` | kubeconfig, RBAC                              | Kubernetes cluster  |

---

## Usage in Workspace

```yaml
kind: workspace
spec:
  providers:
    - ref: @config/providers/aws-primary.yaml
```

---

## Scoping Resources

Resources can target specific providers:

```yaml
kind: resource
spec:
  provider: aws-primary
  region: us-east-1
```

---

## See Also

- `workspace` — uses providers
- `integrations` — terraform and ansible are configured separately
