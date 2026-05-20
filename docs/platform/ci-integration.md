# CI / CD Integration

strata is CI-friendly by design. It provides deterministic exit codes, machine-readable JSON output, and environment variable configuration — everything you need to integrate infrastructure validation and deployment into automated pipelines.

---

## Exit Codes

Every strata command returns one of four exit codes. Use these to control pipeline flow:

| Code | Meaning                                                      | CI Behaviour                              |
| ---- | ------------------------------------------------------------ | ----------------------------------------- |
| `0`  | Success                                                      | Continue pipeline                         |
| `1`  | System / execution failure (crash, missing file, init error) | Fail the build                            |
| `2`  | Usage error — invalid CLI arguments                          | Fail the build (fix your pipeline script) |
| `3`  | Validation failure — file processed but schema-invalid       | Fail the PR gate; block merge             |

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
      - run: strata diff --file $STRATA_FILE --output json > diff.json
        if: github.event_name == 'pull_request'
      - uses: actions/github-script@v7
        if: github.event_name == 'pull_request'
        with:
          script: |
            const fs = require('fs');
            const diff = fs.readFileSync('diff.json', 'utf8');
            const body = `### 🔍 strata diff\n\`\`\`json\n${diff}\n\`\`\``;
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
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv tool install xyz-strata
      - run: strata build run --file $STRATA_FILE
      - run: strata deploy run --file $STRATA_FILE --force
```

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

          - script: strata diff --file $(STRATA_FILE) --output json > $(Build.ArtifactStagingDirectory)/diff.json
            displayName: Generate diff
            condition: eq(variables['Build.Reason'], 'PullRequest')

          - task: PublishBuildArtifacts@1
            inputs:
              PathtoPublish: $(Build.ArtifactStagingDirectory)/diff.json
              ArtifactName: diff
            condition: eq(variables['Build.Reason'], 'PullRequest')
            displayName: Publish diff artifact

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
