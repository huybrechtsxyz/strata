---
name: devops-ci-cd-workflows
description: 'DevOps CI/CD patterns: GitHub Actions workflows, approval gates, testing pipelines, and deployment automation. Supporting context for the strata-specific skills — use when wiring strata commands into a CI/CD pipeline.'
---

# DevOps CI/CD Workflows

## What is CI/CD?

**CI (Continuous Integration):**
- Automatically test code changes when they're committed
- Run linters, unit tests, security scans
- Fast feedback loop (detect problems immediately)

**CD (Continuous Deployment):**
- Automatically deploy code to environments (dev → staging → prod)
- One workflow from commit to production
- Reduces manual handoff errors

**CI/CD Pipeline Flow:**

```
Developer commits code
    ↓
GitHub Actions triggered (automatically)
    ↓
Build & Test (lint, security scan, unit tests)
    ↓
If tests pass:
  - Build artifacts (Docker images, Terraform code)
  - Deploy to DEV environment
    ↓
    If approved by reviewer:
    - Deploy to STAGING environment
    - Run integration tests
      ↓
      If staging OK:
      - Deploy to PRODUCTION
        ↓
        Monitor health, alerts on failure
```

---

## GitHub Actions Basics

GitHub Actions automate tasks triggered by events (push, PR, schedule).

### Workflow File Structure

{% raw %}
```yaml
name: Deploy Infrastructure

on:
  push:
    branches: [main]         # trigger on main branch push
  pull_request:
    branches: [main]         # trigger on PR to main

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - name: Validate YAML
        run: strata validate -f config/prod.yaml --output json

  build:
    needs: validate          # only runs if 'validate' job succeeds
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - name: Build artifacts
        run: strata build run -f deploy/prod.yaml --output json

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: build-artifacts
          path: build/

  deploy-dev:
    needs: build
    runs-on: ubuntu-latest
    environment: development  # approval gate
    steps:
      - uses: actions/checkout@v7

      - name: Deploy to Dev
        run: strata deploy run -f deploy/dev.yaml --force --output json

      - name: Health check
        run: strata deploy health -f deploy/dev.yaml --output json

  deploy-prod:
    needs: deploy-dev
    runs-on: ubuntu-latest
    environment: production    # requires approval
    if: github.ref == 'refs/heads/main'  # only on main branch
    steps:
      - uses: actions/checkout@v7

      - name: Deploy to Production
        run: strata deploy run -f deploy/prod.yaml --force --output json

      - name: Verify deployment
        run: strata deploy health -f deploy/prod.yaml --output json
```
{% endraw %}

### Trigger Events

| Event                 | When                       | Use Case                        |
| ------------------------ | ------------------------------ | ------------------------------------ |
| `push`                 | Code pushed to branch       | Run tests on every commit          |
| `pull_request`         | PR opened or updated         | Validate changes before merge       |
| `schedule`              | Cron expression              | Daily/weekly automated checks       |
| `workflow_dispatch`    | Manual trigger                | On-demand deployments               |
| `release`               | GitHub release published    | Production deployment               |

### Approval Gates (Environments)

Protect production with manual approval:

```yaml
deploy-prod:
  environment: production      # links to GitHub environment
  steps: ...
```

**GitHub Settings → Environments → Production:**
- Add required reviewers (2+ people)
- Set timeout (wait N days for approval)
- Restrict deployment branches (main only)

**Effect:**
- Deployment pauses, waits for approval
- Reviewer must click "Review deployments" in GitHub
- Deployment proceeds only after approval

Strata also has its own deployment-level approval gates (`spec.gates`) that don't require a GitHub environment — see the `strata-deployment-lifecycle` skill and ADR-0059 for the `strata workitem`/`deploy run --resume` hand-off flow (exit code 5).

---

## CI/CD Patterns for Infrastructure

### Pattern 1: Validate on PR, Deploy on Merge

**When PR is opened:**
```yaml
on:
  pull_request:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: strata validate -f deploy/prod.yaml --output json
      - run: strata build plan -f deploy/prod.yaml --output json
```

**Comment on PR with changes:**
{% raw %}
```yaml
  - name: Comment plan on PR
    uses: actions/github-script@v7
    with:
      script: |
        const fs = require('fs');
        const plan = fs.readFileSync('plan.json', 'utf8');
        github.rest.issues.createComment({
          issue_number: context.issue.number,
          owner: context.repo.owner,
          repo: context.repo.repo,
          body: `## Infrastructure Changes\n\`\`\`json\n${plan}\n\`\`\``
        });
```
{% endraw %}

**When PR is merged to main:**
{% raw %}
```yaml
on:
  push:
    branches: [main]

jobs:
  deploy-dev:
    runs-on: ubuntu-latest
    steps:
      - run: strata deploy run -f deploy/dev.yaml --force --output json

  deploy-prod:
    needs: deploy-dev
    environment: production
    steps:
      - run: strata deploy run -f deploy/prod.yaml --force --output json
```
{% endraw %}

---

### Pattern 2: Scheduled Drift Detection

Run weekly to catch infrastructure changes made outside IaC:

{% raw %}
```yaml
name: Weekly Drift Check

on:
  schedule:
    - cron: '0 9 * * MON'  # Every Monday at 9 AM UTC

jobs:
  drift-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - name: Check for drift
        run: strata build plan -f deploy/prod.yaml --output json > plan.json

      - name: Report drift
        if: failure()
        run: |
          echo "Infrastructure drift detected!"
          cat plan.json

      - name: Create issue if drift found
        if: failure()
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: 'Infrastructure Drift Detected',
              body: 'Run `strata deploy run -f deploy/prod.yaml --force` to reconcile'
            });
```
{% endraw %}

---

### Pattern 3: Manual Deployment with Approval

On-demand production deployment:

{% raw %}
```yaml
name: Manual Production Deploy

on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Environment to deploy'
        required: true
        default: 'staging'
        type: choice
        options:
          - staging
          - production

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: ${{ github.event.inputs.environment }}
    steps:
      - uses: actions/checkout@v7

      - name: Deploy to ${{ github.event.inputs.environment }}
        run: strata deploy run -f deploy/${{ github.event.inputs.environment }}.yaml --force --output json
```
{% endraw %}

**Trigger manually:**
1. GitHub → Actions → Manual Production Deploy
2. Click "Run workflow"
3. Select environment
4. Wait for approval
5. Deployment proceeds

---

### Pattern 4: Environment Promotion

Automated promotion through environments:

{% raw %}
```yaml
name: Promotion Pipeline

on:
  push:
    branches: [main]
    paths:
      - 'deploy/**'
      - '.github/workflows/promotion.yml'

jobs:
  deploy-dev:
    runs-on: ubuntu-latest
    steps:
      - run: strata deploy run -f deploy/dev.yaml --force --output json

  test-dev:
    needs: deploy-dev
    runs-on: ubuntu-latest
    steps:
      - run: bash scripts/test-dev.sh

  deploy-staging:
    needs: test-dev
    environment: staging
    runs-on: ubuntu-latest
    steps:
      - run: strata deploy run -f deploy/staging.yaml --force --output json

  test-staging:
    needs: deploy-staging
    runs-on: ubuntu-latest
    steps:
      - run: bash scripts/test-staging.sh

  deploy-prod:
    needs: test-staging
    environment: production
    runs-on: ubuntu-latest
    steps:
      - run: strata deploy run -f deploy/prod.yaml --force --output json

  verify-prod:
    needs: deploy-prod
    runs-on: ubuntu-latest
    steps:
      - run: strata deploy health -f deploy/prod.yaml --output json
      - run: bash scripts/smoke-tests.sh
```
{% endraw %}

---

## Best Practices

### Approval Gates

**Always require approval before production:**

{% raw %}
```yaml
environment:
  name: production
  required_reviewers: 2          # require 2 approvals
  deployment_branch_policy:
    protected_branches: true     # main only
  wait_timer: 15                 # 15 min delay before deployment
```
{% endraw %}

**Why:**
- Catches mistakes before they hit production
- Provides human oversight
- Creates audit trail of who approved what
- Reduces blast radius of bad deployments

### Secrets Management

Store credentials in GitHub Secrets, never in code:

{% raw %}
```yaml
# ✅ RIGHT — use GitHub Secrets
env:
  AZURE_CREDENTIALS: ${{ secrets.AZURE_CREDENTIALS }}

steps:
  - run: strata deploy run -f deploy/prod.yaml --force --output json
```
{% endraw %}

**Never:**
{% raw %}
```yaml
# ❌ WRONG — hardcoded secrets
env:
  DB_PASSWORD: "my-secret-password-123"
```
{% endraw %}

Strata itself resolves secrets from an integration-backed store (Infisical/Vault/Bitwarden/Azure Key Vault) at deploy time — see the `strata-secret-resolution-patterns` skill. GitHub Secrets are only needed for credentials strata's own integrations use to reach those stores (e.g. `INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET`).

### Notifications & Alerts

Alert on deployment success/failure:

{% raw %}
```yaml
- name: Notify Slack on success
  if: success()
  uses: slackapi/slack-github-action@v2
  with:
    webhook-url: ${{ secrets.SLACK_WEBHOOK }}
    payload: |
      {
        "text": "✅ Production deployment succeeded"
      }

- name: Notify Slack on failure
  if: failure()
  uses: slackapi/slack-github-action@v2
  with:
    webhook-url: ${{ secrets.SLACK_WEBHOOK }}
    payload: |
      {
        "text": "❌ Production deployment failed: ${{ job.status }}"
      }
```
{% endraw %}

### Rollback Strategy

**Simple rollback:**
{% raw %}
```yaml
# Revert commit and push
git revert HEAD
git push origin main

# CI/CD automatically deploys the revert
# Infrastructure rolls back to previous state
```
{% endraw %}

Strata itself does not auto-rollback — see the `strata-deployment-lifecycle` skill for provisioner-specific rollback (Terraform destroy/re-apply, Ansible rollback playbooks).

---

## Testing in CI/CD

### Validation Tests (quick)

{% raw %}
```yaml
validate:
  steps:
    - run: strata validate -f deploy/prod.yaml --output json
    - run: strata build plan -f deploy/prod.yaml --output json
```
{% endraw %}

### Security Scanning

{% raw %}
```yaml
security-scan:
  steps:
    - run: checkov -f deploy/prod.yaml
    - run: trivy fs --severity HIGH .
```
{% endraw %}

### Integration Tests

{% raw %}
```yaml
integration-test:
  needs: deploy-dev
  steps:
    - run: bash tests/integration-tests.sh
    - run: bash tests/health-checks.sh
    - run: bash tests/smoke-tests.sh
```
{% endraw %}

---

## Workflow Best Practices

1. **Fail fast** — validate early, don't wait for deploy to fail
2. **Atomic jobs** — each job does one thing well
3. **Clear naming** — "deploy-prod" is clearer than "step3"
4. **Environment consistency** — dev/staging/prod use same workflow steps
5. **Approval gates** — production always requires review
6. **Secrets isolated** — never log or echo secrets
7. **Timeout protection** — prevent workflows from running forever
8. **Rollback ready** — have a documented rollback procedure
9. **Notifications** — alert the team on success/failure
10. **Audit trail** — GitHub Actions logs everything (who, when, what); strata's own `audit list` complements it

---

## Troubleshooting

| Problem                                     | Cause                          | Fix                                                          |
| ------------------------------------------------ | ---------------------------------- | ------------------------------------------------------------------ |
| Workflow doesn't trigger                       | Wrong branch filter              | Check `on: push: branches:` matches your workflow branch          |
| Approval stuck                                  | Reviewer didn't notice            | Set up Slack notifications for pending approvals                   |
| Secrets not available                           | Secret not defined in GitHub      | Add secret in Settings → Secrets and variables → Actions           |
| Deployment failed in CI but works locally      | Environment difference            | Check `STRATA_WORK_PATH`, `.strata/` config differences            |
| Slow validation                                 | Too many checks                    | Run critical checks on PR, full checks on main branch              |
| "Required integration not available" for a store that clearly works | CLI-presence check unrelated to the auth mode actually used (e.g. an API-token-based integration with no CLI installed) — non-fatal, just noisy | Confirm the deploy itself succeeded/failed on a *different* line before chasing this warning |

---

## Agent Best Practices

1. **Understand the pipeline** — know which jobs run when
2. **Test locally before pushing** — save CI/CD time
3. **Write clear commit messages** — helps with audit trail
4. **Review PRs carefully** — approval gate is your last chance
5. **Check deployment logs** — understand what actually happened
6. **Monitor for drift** — schedule regular health checks
7. **Rollback procedures** — have a plan for when things go wrong
8. **Don't skip validation** — dry-run catches 90% of issues
