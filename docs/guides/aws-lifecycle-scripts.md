# AWS Lifecycle Scripts

Lifecycle scripts are Python files strata runs before or after deploy stages. The
`AWSScript` base class gives scripts pre-wired access to the AWS CLI — no setup
required.

## Quick start — use a built-in script

Reference built-in scripts directly from workspace YAML lifecycle hooks:

```yaml
lifecycle:
  pre_deploy:
    scripts:
      # Fetch EKS credentials so Helm / ArgoCD can reach the cluster
      - strata://aws_eks_credentials.py

      # Log Docker into Amazon ECR before image push
      - strata://aws_ecr_login.py

  pre_provision:
    scripts:
      # Ensure S3 bucket exists (idempotent — safe to run every time)
      - strata://aws_s3_bucket_ensure.py
```

> **Tip:** Copy any built-in script to `.strata/scripts/` to customise it.

---

## Built-in scripts

### `aws_eks_credentials.py`

Runs `aws eks update-kubeconfig` to merge cluster credentials into the local
kubeconfig. Use before Helm, ArgoCD, or Flux stages targeting Amazon EKS.

Required variables:

| Variable | Description |
|---|---|
| `EKS_CLUSTER` | EKS cluster name |
| `AWS_DEFAULT_REGION` | AWS region |

Optional variables:

| Variable | Default | Description |
|---|---|---|
| `EKS_ROLE_ARN` | — | IAM role ARN to assume for kubeconfig auth |
| `EKS_CONTEXT_ALIAS` | — | Override kubeconfig context name |
| `EKS_NAMESPACE` | — | Set default namespace in kubeconfig context |

```yaml
variables:
  - key: EKS_CLUSTER
    source: constant
    value: my-eks-cluster
  - key: AWS_DEFAULT_REGION
    source: constant
    value: us-east-1
```

---

### `aws_ecr_login.py`

Runs `aws ecr get-login-password | docker login` so Docker push/pull commands work
against Amazon Elastic Container Registry in the same stage.

Required — one of:
- `ECR_REGISTRY` — full registry URL (e.g. `123456789012.dkr.ecr.us-east-1.amazonaws.com`)
- `ECR_ACCOUNT_ID` + `AWS_DEFAULT_REGION` — registry URL is constructed automatically

Optional:

| Variable | Description |
|---|---|
| `ECR_ACCOUNT_ID` | Account ID (auto-resolved via STS if absent and `ECR_REGISTRY` not set) |

```yaml
variables:
  - key: ECR_REGISTRY
    source: constant
    value: 123456789012.dkr.ecr.us-east-1.amazonaws.com
```

---

### `aws_s3_bucket_ensure.py`

Runs `aws s3api create-bucket` — idempotent: if the bucket already exists and is
owned by the same account the command succeeds without changes. Use before a Terraform
deploy that needs the remote state bucket to exist.

Required variables:

| Variable | Description |
|---|---|
| `S3_BUCKET` | S3 bucket name |
| `AWS_DEFAULT_REGION` | AWS region |

Optional variables:

| Variable | Default | Description |
|---|---|---|
| `S3_VERSIONING` | `false` | Set to `true` to enable versioning (recommended for Terraform state) |
| `S3_ENCRYPTION` | `false` | Set to `true` to enable AES-256 server-side encryption |
| `S3_BLOCK_PUBLIC` | `true` | Public access block (recommended; set `false` only for public buckets) |
| `S3_TAGS` | — | Comma-separated `key=value` tags (e.g. `env=prod,team=platform`) |

```yaml
variables:
  - key: S3_BUCKET
    source: constant
    value: my-terraform-state
  - key: S3_VERSIONING
    source: constant
    value: "true"
  - key: S3_ENCRYPTION
    source: constant
    value: "true"
```

---

## Write a custom script

Subclass `AWSScript` in `.strata/scripts/`:

```python
# .strata/scripts/pre_deploy_custom.py
from strata.utils.aws_script_base import AWSScript

class MyPreDeploy(AWSScript):
    def run(self):
        bucket = self.require_env("S3_BUCKET")   # exits with error if absent
        region = self.region()                    # resolves from env/profile or exits

        result = self.run_aws(["s3api", "head-bucket", "--bucket", bucket,
                               "--region", region])
        self.exit_on_failure(result, "aws s3api head-bucket")
        self.log(f"Bucket '{bucket}' confirmed in '{region}'")

if __name__ == "__main__":
    MyPreDeploy().execute()
```

Reference it in workspace YAML:

```yaml
lifecycle:
  pre_deploy:
    scripts:
      - .strata/scripts/pre_deploy_custom.py
```

---

## `AWSScript` reference

| Method | Description |
|---|---|
| `run_aws(args, timeout=120)` | Run `aws` subcommand; returns `subprocess.CompletedProcess` |
| `exit_on_failure(result, label)` | `sys.exit(1)` if `returncode != 0`; prints stdout/stderr |
| `require_env(name)` | Return env var value or `sys.exit(1)` with a clear error message |
| `env(name, default="")` | Return env var value with optional default |
| `region()` | `AWS_DEFAULT_REGION` → `AWS_REGION` → `aws configure get region` → exit(1) |
| `account_id()` | AWS account ID from `aws sts get-caller-identity` |
| `workspace_path()` | `Path` from `STRATA_WORKSPACE_PATH` |
| `build_path()` | `Path` from `STRATA_BUILD_PATH` |
| `stage_name()` | Stage name from `STRATA_STAGE_NAME` |
| `log(msg)` | Print to stderr — visible in strata console output |

## Environment variables in all lifecycle scripts

strata injects these before running any script:

| Variable | Content |
|---|---|
| `STRATA_PHASE` | Current lifecycle phase (e.g. `pre_deploy`) |
| `STRATA_WORKSPACE_PATH` | Workspace root path |
| `STRATA_BUILD_PATH` | Build artifacts directory |
| `STRATA_CONFIG_PATH` | `.strata/` state directory |
| `STRATA_STAGE_NAME` | Current stage name |
| _all resolved variables_ | Secrets and variables from the active deployment |
