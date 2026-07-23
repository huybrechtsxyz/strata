# AWS Lifecycle Scripts

`AWSScript` is the AWS equivalent of `AzureScript` — a Python base class for
lifecycle scripts in `.strata/scripts/` with pre-wired AWS CLI helpers.

## Three built-in scripts (no code needed)

```yaml
lifecycle:
  pre_deploy:
    scripts:
      - strata://aws_eks_credentials.py    # aws eks update-kubeconfig
      - strata://aws_ecr_login.py          # aws ecr get-login-password | docker login
  pre_provision:
    scripts:
      - strata://aws_s3_bucket_ensure.py   # aws s3api create-bucket (idempotent)
```

### `aws_eks_credentials.py`
Required: `EKS_CLUSTER`, `AWS_DEFAULT_REGION`
Optional: `EKS_ROLE_ARN`, `EKS_CONTEXT_ALIAS`, `EKS_NAMESPACE`

### `aws_ecr_login.py`
Required: `ECR_REGISTRY` or `ECR_ACCOUNT_ID` + `AWS_DEFAULT_REGION`
Optional: nothing — account and region auto-resolved

### `aws_s3_bucket_ensure.py`
Required: `S3_BUCKET`, `AWS_DEFAULT_REGION`
Optional: `S3_VERSIONING=true`, `S3_ENCRYPTION=true`, `S3_BLOCK_PUBLIC=true`, `S3_TAGS=k=v,k2=v2`

## Write a custom script

```python
# .strata/scripts/my_aws_script.py
from strata.utils.aws_script_base import AWSScript

class MyScript(AWSScript):
    def run(self):
        bucket = self.require_env("S3_BUCKET")
        region = self.region()              # exits if region not set
        account = self.account_id()         # aws sts get-caller-identity
        result = self.run_aws(["s3", "ls", f"s3://{bucket}"])
        self.exit_on_failure(result, "aws s3 ls")

if __name__ == "__main__":
    MyScript().execute()
```

## `AWSScript` reference

| Method                    | Description                                                            |
| ------------------------- | ---------------------------------------------------------------------- |
| `run_aws(args)`           | Run `aws` subcommand; returns `subprocess.CompletedProcess`            |
| `exit_on_failure(result)` | `sys.exit(1)` if `returncode != 0`                                     |
| `require_env(name)`       | Get env var or `sys.exit(1)`                                           |
| `env(name, default="")`   | Get env var with default                                               |
| `region()`                | AWS_DEFAULT_REGION → AWS_REGION → `aws configure get region` → exit(1) |
| `account_id()`            | Account ID from `aws sts get-caller-identity`                          |
| `workspace_path()`        | Path from `STRATA_WORKSPACE_PATH`                                      |
| `log(msg)`                | Print to stderr (visible in strata output)                             |
