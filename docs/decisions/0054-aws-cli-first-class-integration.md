# AWS CLI (`aws`) as a First-Class Integration

- Status: partially-implemented — Phase 1 done, Phase 2 not started
- Date: 2026-07-23
- Related: ADR-0048 (CDK provisioner), ADR-0051 (Checkov pattern), ADR-0053 (az CLI pattern)

## Remaining Work

- Phase 2 integrations not built: `AWSSecretsManagerIntegration`, `AWSSsmIntegration`
- CDK deployer (Phase 2, depends on ADR-0048)

## Context and Problem Statement

strata's AWS support is minimal today:

| Component               | How it uses AWS                                        |
| ----------------------- | ------------------------------------------------------ |
| `terraform_deployer.py` | Assumes AWS credentials are set up externally          |
| `auth_models.py`        | `AWSAuthenticationModel` (schema only, no integration) |

There are **no built-in AWS integrations** for secrets (SecretsManager), variables (SSM
Parameter Store), or container registry (ECR). Operators configure Terraform to use AWS
providers directly, but strata itself never checks whether `aws` is installed or
authenticated — operations fail with unhelpful subprocess errors when credentials are
missing.

**The opportunity:** `aws` is a single entry point to the entire AWS platform. One
integration that validates availability and authentication gives all current and future
AWS-related features a shared foundation.

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

### Current: no AWS-specific integrations

There are no `aws_secretsmanager.py`, `aws_ssm.py`, or other AWS integrations today.
Terraform handles AWS provider authentication externally; strata trusts that credentials
are pre-configured before `strata deploy run` is called.

### AWSCLIIntegration: foundation for future AWS integrations

```
AWSCLIIntegration(BaseIntegration)
    COMMAND = "aws"
    ensure_available()     → aws sts get-caller-identity (confirms login + account)
    get_identity()         → current account id, userId, Arn
    get_region()           → current default region from config
    ↓ Foundation for future AWS integrations:
    ├── CDKDeployer                  (cdk deploy via aws credentials) — ADR-0048
    ├── CloudFormationDeployer       (aws cloudformation create/update-stack)
    ├── AWSSecretsManagerIntegration (future — not yet implemented)
    ├── AWSSsmIntegration            (future — not yet implemented)
    ├── EKS credential setup         (aws eks update-kubeconfig)
    └── ECR login                    (aws ecr get-login-password)
```

**Key principle:** `AWSCLIIntegration` is a tool-availability + auth check and the shared
foundation for future AWS integrations. There are currently **no** `AWSSecretsManagerIntegration`
or `AWSSsmIntegration` classes — those are planned future work. The CLI integration provides:

1. **Tools view status** — "AWS CLI ✅ authenticated (account: 123456789012 / eu-west-1)" vs "❌ not authenticated"
2. **Account validation** — confirms the right account and region are active before deploy
3. **Shared foundation for CDK/CloudFormation deployer** — no bootstrap complexity
4. **Credential chain visibility** — operators see which profile/role is active

### Impact on existing integrations

| Integration                | Change needed?             | How                                            |
| -------------------------- | -------------------------- | ---------------------------------------------- |
| Terraform `aws` provider   | No                         | Uses own auth (env vars or credentials file)   |
| CDK (ADR-0048)             | **Uses AWSCLIIntegration** | `cdk deploy` depends on `aws` credential chain |
| AWSSecretsManager (future) | Will use this              | Not yet implemented                            |
| AWSSSM (future)            | Will use this              | Not yet implemented                            |

Phase 2 future integrations (SecretsManager, SSM) will call `AWSCLIIntegration.ensure_available()`
as their auth check — surfacing credential problems in the Tools view before any secrets
are resolved.

## Design

### `AWSCLIIntegration(BaseIntegration)`

```python
class AWSCLIIntegration(BaseIntegration):
    COMMAND = "aws"
    CAPABILITIES = []  # capability name: "aws"

    def ensure_available(self) -> Tuple[bool, str]:
        """Check aws is installed AND authenticated (aws sts get-caller-identity succeeds)."""

    def get_identity(self) -> Optional[Dict[str, str]]:
        """Return {Account, UserId, Arn} from aws sts get-caller-identity."""

    def get_region(self) -> Optional[str]:
        """Return region: AWS_DEFAULT_REGION → AWS_REGION → aws configure get region."""

    def run_aws(self, args, timeout=120):
        """Run arbitrary aws subcommand — used by lifecycle scripts and future deployers."""
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

### Phase 1 — Integration + CDK/CloudFormation deployer foundation ✅
1. `src/strata/integrations/aws_cli.py` — `AWSCLIIntegration` ✅
   - `ensure_available()`: binary check + `aws sts get-caller-identity`
   - `get_identity()`: returns `{Account, UserId, Arn}`
   - `get_region()`: `AWS_DEFAULT_REGION` → `AWS_REGION` → `aws configure get region`
   - `run_aws(args)`: passthrough for lifecycle scripts and future deployers
2. Register `aws_cli` in `IntegrationFactory._BUILTIN_CLASS_MAP` ✅
3. `IAWSTool` capability protocol + `"aws"` in `CAPABILITY_MAP` ✅
4. Help files: `aws_cli.md`, `aws_scripts.md` ✅
5. Tests: 33 unit tests ✅

**AWSScript base class + built-in lifecycle scripts (also Phase 1):**
- `strata.utils.aws_script_base.AWSScript` — mirrors `AzureScript`; adds `region()` (3-tier resolution) and `account_id()` ✅
- `aws_eks_credentials.py` — `aws eks update-kubeconfig`; `EKS_CLUSTER`, `AWS_DEFAULT_REGION`, optional `EKS_ROLE_ARN` ✅
- `aws_ecr_login.py` — `get-login-password | docker login`; accepts `ECR_REGISTRY` or auto-constructs from `ECR_ACCOUNT_ID` ✅
- `aws_s3_bucket_ensure.py` — idempotent `aws s3api create-bucket` + versioning + encryption + public block + tags ✅
- Solution scaffold: `.strata/scripts/aws_lifecycle_example.py` ✅
- Guide: `docs/guides/aws-lifecycle-scripts.md` ✅

### Phase 2 — CDK deployer (uses AWSCLIIntegration)
- See ADR-0048 for full CDK deployer design
- `CDKDeployer(BaseDeployer)` calls `AWSCLIIntegration` to validate credentials before `cdk deploy`

### Phase 3 \u2014 AWS secret/variable integrations (future)
- `AWSSecretsManagerIntegration` \u2014 `aws secretsmanager get-secret-value` via REST; uses `AWSCLIIntegration` for auth
- `AWSSsmIntegration` \u2014 `aws ssm get-parameter` via REST; uses `AWSCLIIntegration` for auth
- Single credential check per session; centralised in `AWSCLIIntegration.ensure_available()`

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
