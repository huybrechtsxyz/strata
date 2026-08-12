# CI / CD Integration

strata is CI-friendly by design. It provides deterministic exit codes, machine-readable JSON output, and environment variable configuration — everything you need to integrate infrastructure validation and deployment into automated pipelines.

---

## Exit Codes

Every strata command returns one of five exit codes. Use these to control pipeline flow:

| Code | Meaning                                                      | CI Behaviour                               |
| ---- | ------------------------------------------------------------ | ------------------------------------------ |
| `0`  | Success                                                      | Continue pipeline                          |
| `1`  | System / execution failure (crash, missing file, init error) | Fail the build                             |
| `2`  | Usage error — invalid CLI arguments                          | Fail the build (fix your pipeline script)  |
| `3`  | Validation failure — file processed but schema-invalid       | Fail the PR gate; block merge              |
| `4`  | Lock conflict — deployment locked by another process         | Retry deployment after delay (use backoff) |

> **Tip:** Exit code `3` is the most actionable in CI — it means the YAML was parsed but failed validation. Display the JSON output as a PR comment so authors can fix issues without digging through logs.

---

## Machine-Readable Output

Pass `--output json` to any command to get structured JSON on stdout:

```json
{
  "success": true,
  "data": {},
  "errors": [],
  "messages": []
}
```

When `success` is `false`, the `errors` array contains structured error objects:

```json
{
  "success": false,
  "data": null,
  "errors": [
    {
      "code": "VALIDATION_ERROR",
      "message": "Field 'spec.provider' is required",
      "file": "deploy/deploy-prd.yaml",
      "line": 12
    }
  ],
  "messages": []
}
```

In CI scripts, parse this with `jq`, PowerShell `ConvertFrom-Json`, or your language's JSON library.

---

## Environment Variables

Set these once in your pipeline environment to avoid repeating flags on every command:

| Variable           | Equivalent Flag | Description                               |
| ------------------ | --------------- | ----------------------------------------- |
| `STRATA_FILE`      | `--file`        | Default deployment file path              |
| `STRATA_WORK_PATH` | `--work-path`   | Workspace root directory                  |
| `STRATA_OUTPUT`    | `--output`      | Output format (`console`, `text`, `json`) |

```yaml
# GitHub Actions example
env:
  STRATA_FILE: deploy/deploy-prd.yaml
  STRATA_WORK_PATH: ${{ github.workspace }}
```

---

## GitHub Actions

A complete workflow that validates on PRs, posts diff output as a comment, and deploys on merge to `main`:

```yaml
name: Infrastructure CI

on:
  pull_request:
    paths: ['deploy/**', 'envs/**', 'stack/**']
  push:
    branches: [main]

env:
  STRATA_FILE: deploy/deploy-prd.yaml

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv tool install xyz-strata
      - run: strata validate --file $STRATA_FILE --output json
        id: validate
      - run: strata build plan --file $STRATA_FILE --output json > plan.json
        if: github.event_name == 'pull_request'
      - uses: actions/github-script@v7
        if: github.event_name == 'pull_request'
        with:
          script: |
            const fs = require('fs');
            const diff = fs.readFileSync('plan.json', 'utf8');
            const body = `### 🔍 strata build plan\n\`\`\`json\n${diff}\n\`\`\``;
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body
            });

  deploy:
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    needs: validate
    runs-on: ubuntu-latest
    environment: production
    permissions:
      contents: read
      id-token: write  # required for OIDC; harmless when using client-secret mode
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv tool install xyz-strata
      - uses: huybrechtsxyz/strata/.github/actions/azure-login@v0
        with:
          azure_tenant_id: ${{ vars.AZURE_TENANT_ID }}
          azure_subscription_id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
          azure_client_id: ${{ vars.AZURE_CLIENT_ID }}
          # azure_client_secret: ${{ secrets.AZURE_CLIENT_SECRET }}  # omit for OIDC
      - run: strata build run --file $STRATA_FILE
      - run: strata deploy run --file $STRATA_FILE --force
```

---

### Azure authentication

The `azure-login` composite action supports two authentication modes, selected automatically from the inputs you provide. In OIDC mode (preferred), no secret is stored in GitHub — Azure issues a short-lived token scoped to that specific run, which is auditable in Azure Entra ID sign-in logs; this requires `permissions: id-token: write` on the calling job and a federated credential configured in your App Registration (see the [Setting Up Azure OIDC guide](../guides/setup-azure-oidc.md)). In client-secret mode (fallback), pass `azure_client_secret: ${{ secrets.AZURE_CLIENT_SECRET }}` — the action detects the input and switches modes automatically, with no other changes required. If no Azure stores are configured for your deployment, omit all `azure_*` inputs and remove the login step entirely.

> **Tip:** Start with client-secret mode to validate the role assignments and pipeline wiring, then switch to OIDC once everything works — it requires only removing the secret and adding the federated credential in Azure.

---

## Azure Pipelines

An equivalent pipeline for Azure DevOps:

```yaml
trigger:
  branches:
    include: [main]
  paths:
    include:
      - deploy/*
      - envs/*
      - stack/*

pr:
  paths:
    include:
      - deploy/*
      - envs/*
      - stack/*

variables:
  STRATA_FILE: deploy/deploy-prd.yaml

stages:
  - stage: Validate
    jobs:
      - job: ValidateConfig
        pool:
          vmImage: ubuntu-latest
        steps:
          - task: UsePythonVersion@0
            inputs:
              versionSpec: '3.13'

          - script: |
              pip install uv
              uv tool install xyz-strata
            displayName: install xyz-strata

          - script: strata validate --file $(STRATA_FILE) --output json
            displayName: Validate deployment file

          - script: strata build plan --file $(STRATA_FILE) --output json > $(Build.ArtifactStagingDirectory)/plan.json
            displayName: Generate plan
            condition: eq(variables['Build.Reason'], 'PullRequest')

          - task: PublishBuildArtifacts@1
            inputs:
              PathtoPublish: $(Build.ArtifactStagingDirectory)/plan.json
              ArtifactName: plan
            condition: eq(variables['Build.Reason'], 'PullRequest')
            displayName: Publish plan artifact

  - stage: Deploy
    dependsOn: Validate
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
    jobs:
      - deployment: DeployProduction
        pool:
          vmImage: ubuntu-latest
        environment: production
        strategy:
          runOnce:
            deploy:
              steps:
                - checkout: self

                - script: |
                    pip install uv
                    uv tool install xyz-strata
                  displayName: install xyz-strata

                - script: strata build run --file $(STRATA_FILE)
                  displayName: Build artifacts

                - script: strata deploy run --file $(STRATA_FILE) --force
                  displayName: Deploy to production
```

---

## Tips

### Resolved-model cache for fast multi-deployment pipelines

Every `strata build` and `strata deploy` command uses a SQLite cache (`.strata/cache.db`) to avoid redundant variable/feature/environment resolution and build artifact regeneration when configuration hasn't changed.

Cache keys are based on deployment + environment + tenant YAML content hashes — changes invalidate automatically. Use `--refresh-cache` to force a rebuild, or `--no-cache` to bypass entirely.

**Three cache kinds:**

| Kind                   | What's cached                                                      | Used by                                                |
| ---------------------- | ------------------------------------------------------------------ | ------------------------------------------------------ |
| `deployment`           | Build artifacts (Terraform `.tfvars.json`, Helm values, etc.)      | `strata build run/plan`; `strata deploy run/plan`      |
| `resolved_environment` | Merged environment model (variables/secrets/features declarations) | `strata deploy show`, `strata values *`                |
| `resolved_values`      | Resolved variable and feature flag values (excludes secrets)       | `strata deploy show`, `strata values list/get/resolve` |

**CI Pipeline Benefits:**

1. **Warm the cache once** — First job (e.g. validation) writes cache entries
2. **Subsequent jobs reuse** — Deploy and inspection jobs hit cache, skip store queries
3. **Automatic invalidation** — YAML content hash changes = cache miss = fresh resolve
4. **Optional refresh** — Use `--refresh-cache` in cleanup/refresh jobs if you need fresh secret values despite no config change

**Example multi-stage GitHub Actions workflow:**

```yaml
jobs:
  validate-and-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: strata validate -f deploy/deploy-prd.yaml --deep
      - run: strata build run -f deploy/deploy-prd.yaml
      # Cache is now warm for this deployment

  deploy:
    needs: validate-and-build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # No --refresh-cache needed — same YAML hash hits cache from validate-and-build
      - run: strata deploy run -f deploy/deploy-prd.yaml --force

  inspect:
    needs: deploy
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Cache hit — no store queries, instant values lookup
      - run: strata values list -f deploy/deploy-prd.yaml --output json
```

For details, see [Caching](../guides/caching.md).

### Cache uv packages

Speed up pipeline runs by caching the uv tool directory:

```yaml
# GitHub Actions
- uses: actions/cache@v4
  with:
    path: ~/.local/share/uv
    key: uv-${{ runner.os }}-${{ hashFiles('**/pyproject.toml') }}
```

```yaml
# Azure Pipelines
- task: Cache@2
  inputs:
    key: 'uv | "$(Agent.OS)" | **/pyproject.toml'
    path: $(HOME)/.local/share/uv
```

### Run in Docker

If you publish a strata CLI image, skip the install step entirely:

```yaml
# GitHub Actions
jobs:
  validate:
    runs-on: ubuntu-latest
    container: ghcr.io/org/strata-cli:latest
    steps:
      - uses: actions/checkout@v4
      - run: strata validate --file $STRATA_FILE --output json
```

### Parallelise validation across multiple files

When your workspace contains multiple deployment files, validate them in parallel:

```yaml
# GitHub Actions — matrix strategy
jobs:
  validate:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        file:
          - deploy/deploy-prd.yaml
          - deploy/deploy-stg.yaml
          - deploy/deploy-dev.yaml
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv tool install xyz-strata
      - run: strata validate --file ${{ matrix.file }} --output json
```

### Suppress console noise

Use `--quiet` in deploy steps where you only care about the exit code:

```bash
strata deploy run --file $STRATA_FILE --force --quiet
```

Or combine `--output json` with `--quiet` to get only the JSON envelope with no progress messages on stderr.

---

## State Locking in CI

Locking prevents two pipelines from deploying or destroying the same environment at the
same time. Enable it by adding a `locking` block to your deployment file:

```yaml
spec:
  locking:
    enabled: true
    strategy: wrap       # strata acquires the lock (default)
    wait_timeout: "10m"  # how long a second pipeline waits before failing
```

When a second pipeline starts while a deploy is in progress, strata exits with **code 1**
and prints:

```
🔒  Could not acquire lock — held by 'ci-bot@runner-07'.
    Run `strata deploy lock status` for details.
```

### Recovering from a crashed pipeline

If a pipeline is killed (OOM, timeout, node reboot) the lock may stay held. Use
`--force-lock` on the next run to break the stale lock and proceed:

```bash
# GitHub Actions
strata deploy run --file $STRATA_FILE --force --force-lock

# Azure Pipelines
strata deploy run --file $(STRATA_FILE) --force --force-lock
```

Or use the standalone release command (useful from a maintenance step):

```bash
strata deploy lock release --file $STRATA_FILE --force
```

### GitHub Actions — concurrent run guard

Combine `concurrency:` with strata locking for defence-in-depth:

```yaml
# .github/workflows/deploy.yml
jobs:
  deploy:
    concurrency:
      group: deploy-production
      cancel-in-progress: false   # queue, not cancel
    steps:
      - run: strata deploy run --file deploy/deploy-prd.yaml --force
```

The `concurrency` group ensures only one GitHub Actions run is active at a time.
strata's lock provides a second layer that protects against runs from different
workflow files, manual triggers, or external CI systems hitting the same environment.

### Azure Pipelines — exclusive lock

Use an Azure DevOps **Exclusive Lock** on the `environment` resource in combination
with strata locking:

```yaml
- stage: Deploy
  jobs:
    - deployment: DeployProduction
      environment:
        name: production
        resourceType: VirtualMachine  # or Kubernetes, etc.
      # ADO environments support an "Exclusive Lock" check in the UI —
      # enable it to prevent concurrent deployments within this pipeline.
      strategy:
        runOnce:
          deploy:
            steps:
              - script: strata deploy run --file deploy/deploy-prd.yaml --force
                displayName: Deploy to production
```

### Checking lock status from CI

```bash
# Exit 0 if locked, 1 if not
strata deploy lock status --file $STRATA_FILE --output json

# Show history (useful in post-failure diagnostics)
strata deploy lock history --file $STRATA_FILE --last 5
```

For a full walk-through of lock backends, strategies, and manual management see
[How Deployment Locking Works](../guides/how-deployment-locking-works.md).
