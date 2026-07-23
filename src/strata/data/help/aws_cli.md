# AWS CLI Integration

The AWS CLI integration (`type: aws_cli`) is the shared foundation for all AWS CLI-based
operations in strata. It checks that `aws` is installed **and authenticated**
(`aws sts get-caller-identity`), and exposes account identity and region context.

Installation
```
# macOS
brew install awscli

# Linux / Windows
https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
```

Verify install
```
aws --version
aws sts get-caller-identity
```

Authentication

| Method                          | Setup                                                                  |
| ------------------------------- | ---------------------------------------------------------------------- |
| **aws configure**               | Interactive profile setup. Preferred for local dev.                    |
| **Environment variables**       | Set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` |
| **IAM role / instance profile** | Automatic on EC2/ECS/Lambda — no env vars needed                       |
| **AWS SSO**                     | `aws sso login --profile <profile>`                                    |

Key environment variables

| Variable                | Purpose                                 |
| ----------------------- | --------------------------------------- |
| `AWS_ACCESS_KEY_ID`     | Access key ID                           |
| `AWS_SECRET_ACCESS_KEY` | Secret access key                       |
| `AWS_SESSION_TOKEN`     | Session token (temporary credentials)   |
| `AWS_DEFAULT_REGION`    | Default AWS region                      |
| `AWS_PROFILE`           | Named profile from `~/.aws/credentials` |

Configuration YAML

```yaml
integrations:
  - name: aws
    type: aws_cli
    capabilities: [aws]
    required: true
```

What `ensure_available()` checks

1. `aws` binary in PATH — if not: "AWS CLI not installed"
2. `aws sts get-caller-identity` succeeds — if not: "Not authenticated"

Tools view shows:
- ✅ `aws_cli — Authenticated (account: 123456789012, region: us-east-1)`
- ❌ `aws_cli — not authenticated (run: aws configure)`

Common checks
```
aws sts get-caller-identity        # verify active identity
aws configure list                 # show current config
aws configure list-profiles        # list all profiles
aws ec2 describe-regions           # verify region access
```

Docs
- https://docs.aws.amazon.com/cli/
- Authentication: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html
