# AWS CLI (`aws`) as a First-Class Integration

- Status: proposed
- Date: 2026-07-23
- Related: ADR-0048 (CDK provisioner), ADR-0051 (Checkov pattern), ADR-0053 (az CLI pattern)

## Context and Problem Statement

strata's AWS support is currently spread across:

| Component               | How it uses AWS                                        |
| ----------------------- | ------------------------------------------------------ |
| `aws_secretsmanager.py` | REST API + `aws` CLI for secret resolution             |
| `aws_ssm.py`            | REST API + `aws` CLI for parameter/variable resolution |
| `terraform_deployer.py` | Assumes AWS CLI auth for `awscc` / `aws` provider      |
| ADR-0048 (CDK)          | Needs `cdk deploy` which requires `aws` credentials    |

None of these check whether `aws` is actually installed, authenticated, or targeting the
right account and region. Each integration independently attempts AWS operations and fails
with unhelpful subprocess errors when `aws` is unavailable or not authenticated.

**The opportunity:** `aws` is a single entry point to the entire AWS platform. One integration
that validates availability and authentication gives all AWS-related features a shared
foundation — including the CDK provisioner (ADR-0048) and CloudFormation-based deployers.

## What `aws` Enables Beyond CDK

| Capability           | `aws` subcommand                        | Use in strata                              |
| -------------------- | --------------------------------------- | ------------------------------------------ |
| CDK / CloudFormation | `aws cloudformation describe-stacks`    | CDK provisioner (ADR-0048)                 |
| Deployment drift     | `aws cloudformation detect-stack-drift` | `strata deploy drift` for CloudFormation   |
| Deployment history   | `aws cloudformation list-stack-events`  | `strata deploy history` for CloudFormation |
| EKS credentials      | `aws eks update-kubeconfig`             | Pre-deploy setup for Helm/ArgoCD           |
| Container registry   | `aws ecr get-login-password`, `aws ecr` | Container push before deploy               |
| Account context      | `aws sts get-caller-identity`           | Confirm authenticated account and region   |
| Assume role          | `aws sts assume-role`                   | Cross-account fleet management             |
| Access token         | `aws sts get-session-token`             | Token for REST API calls                   |
| CloudWatch / X-Ray   | `aws cloudwatch get-metric-statistics`  | Health checks, observability               |

## Relationship to Existing AWS Integrations

### Current: SDK-first, `aws` as fallback

```
AWSSecretsManagerIntegration  → REST API (urllib) → uses aws CLI credentials if available
AWSSsmIntegration             → REST API (urllib) → uses aws CLI credentials if available
```

Both integrations independently resolve credentials — either from environment variables
(`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`) or from the shared credentials file
managed by `aws configure`.

### Proposed: `AWSCLIIntegration` as shared auth foundation

```
AWSCLIIntegration(BaseIntegration)
    COMMAND = "aws"
    ensure_available()     → aws sts get-caller-identity (confirms login + account)
    get_account()          → current account id + alias
    get_region()           → current default region from config
    ↓ Used by:
    ├── CDKDeployer                  (cdk deploy via aws credentials)
    ├── CloudFormationDeployer       (aws cloudformation create/update-stack)
    ├── AWSSecretsManagerIntegration (credential chain validation)
    ├── AWSSsmIntegration            (credential chain validation)
    ├── EKS credential setup         (aws eks update-kubeconfig)
    └── ECR login                    (aws ecr get-login-password)
```

**Key principle:** `AWSCLIIntegration` is a tool-availability + auth check, not a
replacement for the existing SDK-based integrations. SecretsManager and SSM continue using
REST directly (faster, no subprocess per secret). The CLI integration provides:

1. **Tools view status** — "AWS CLI ✅ authenticated (account: 123456789012 / eu-west-1)" vs "❌ not authenticated"
2. **Account validation** — confirms the right account and region are active before deploy
3. **Shared foundation for CDK/CloudFormation deployer** — no bootstrap complexity
4. **Credential chain visibility** — operators see which profile/role is active

### Impact on existing integrations

| Integration                        | Change needed?             | How                                            |
| ---------------------------------- | -------------------------- | ---------------------------------------------- |
| `AWSSecretsManagerIntegration`     | No (Phase 1)               | Continues working as-is                        |
| `AWSSsmIntegration`                | No (Phase 1)               | Continues working as-is                        |
| Terraform `aws` / `awscc` provider | No                         | Uses own auth (env vars or credentials file)   |
| CDK (ADR-0048)                     | **Uses AWSCLIIntegration** | `cdk deploy` depends on `aws` credential chain |

Future (Phase 2): SecretsManager and SSM integrations could delegate credential validation
to `AWSCLIIntegration.ensure_available()` — centralising the check and surfacing auth
problems in the Tools view before any secrets are resolved.

## Design

### `AWSCLIIntegration(BaseIntegration)`

```python
class AWSCLIIntegration(BaseIntegration):
    COMMAND = "aws"
    CAPABILITIES = []  # capability name: "aws"

    def ensure_available(self) -> Tuple[bool, str]:
        """Check aws is installed AND authenticated (aws sts get-caller-identity succeeds)."""

    def get_account(self) -> Optional[Dict[str, str]]:
        """Return {account, arn, userId} from aws sts get-caller-identity."""

    def get_region(self) -> Optional[str]:
        """Return the default region from aws configure get region."""
```

### Configuration YAML

```yaml
integrations:
  - name: aws
    type: aws_cli
    capabilities: [aws]
    required: true        # or false — depends on whether AWS is the target
    validation:
      command: aws sts get-caller-identity
```

No `endpoints` or `authentication` block needed — `aws` uses its own credential chain
(instance profile → environment variables → shared credentials file → SSO login).

### Tools view output

```
aws_cli      2.17.0    ✅    (account: 123456789012 / eu-west-1)
```

When not authenticated:
```
aws_cli      2.17.0    ⚠️    not authenticated (run: aws configure or aws sso login)
```

### What `ensure_available()` checks

1. `aws` binary in PATH → if not: "AWS CLI not installed"
2. `aws sts get-caller-identity --output json` succeeds → if not: "Not authenticated (run `aws configure` or `aws sso login`)"
3. Returns account ID and region for display in Tools view

This mirrors the stronger check used by `AzureCLIIntegration` (ADR-0053):
binary-without-credentials is useless — operators need to know immediately.

## Implementation Plan

### Phase 1 — Integration + CDK/CloudFormation deployer foundation
1. `src/strata/integrations/aws_cli.py` — `AWSCLIIntegration`
2. Register `aws_cli` in `IntegrationFactory._BUILTIN_CLASS_MAP`
3. Help file: `src/strata/data/help/aws_cli.md`
4. Tests for `ensure_available()`, `get_account()`, `get_region()`

### Phase 2 — CDK deployer (uses AWSCLIIntegration)
- See ADR-0048 for full CDK deployer design
- `CDKDeployer(BaseDeployer)` calls `AWSCLIIntegration` to validate credentials before `cdk deploy`

### Phase 3 — Credential unification (optional)
- `AWSSecretsManagerIntegration.ensure_available()` delegates to `AWSCLIIntegration.ensure_available()`
- `AWSSsmIntegration.ensure_available()` same
- Single credential check per session

## Consequences

### Positive
- **Single source of truth** for AWS CLI availability and auth status
- **Tools view** shows clear "authenticated / not authenticated" with account context at a glance
- **CDK deployer** gets a pre-validated `aws` credential foundation — no bootstrap complexity
- **Account + region confirmation** — operators know which account will be targeted before deploy
- **Multi-account fleet management** — `aws sts assume-role` support unlocks cross-account operations

### Negative
- `aws sts get-caller-identity` is slightly slower than a simple `aws --version` check
- Operators using AWS SSO must run `aws sso login` separately before strata — but this is already true for Terraform

### Neutral
- Existing SecretsManager/SSM integrations continue to work unchanged (no migration needed)
- AWS CLI v2 is required; v1 is EOL and not supported
