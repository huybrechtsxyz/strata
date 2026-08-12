# GitHub pull request integration

- Status: implemented — reusable workflow (`strata-validate-pr.yml`) published with exit-code mapping and `gh pr comment` integration; GitHub Enterprise Server support verified (`gh` CLI respects `GITHUB_API_URL` as expected)
- Date: 2026-07-11 (revised 2026-08-12 — scoped to CLI exit codes + reusable workflow templates, not a GitHub App)

## Context and Problem Statement

Infrastructure changes in a strata config workspace go through Git pull requests, but strata doesn't participate in the PR lifecycle today. Reviewers can't see what a change will do (validation errors, plan diff, cost impact, policy violations) without cloning the branch and running strata locally.

The temptation is to build a GitHub App — a hosted service with webhooks, a bot identity, and managed permissions. But that's a product, not a tool, and it conflicts with strata's core design: **strata is a CLI that operators invoke; it doesn't own their pipeline.** The person building the CI knows when to run strata, what to do with failures, and what blocking vs. advisory means for their org. strata's job is to exit with a clear code and produce structured output; GitHub Actions (or any CI) decides what to do with that.

## Decision Drivers

- **Pipeline owners know their workflow** — strata should not dictate when validation runs, whether it blocks merge, or how plan output is displayed. Different teams have different policies.
- **Exit codes are the universal CI contract** — exit 0 = pass, exit 3 = validation failure. Every CI system understands this without a strata-specific integration.
- **GitHub Enterprise Server must work** — many organisations run GHE on-prem. A GitHub App registered on github.com doesn't help them. `gh` CLI and `GITHUB_API_URL` work everywhere.
- **No hosted infrastructure** — strata is installed via `pip install xyz-strata` (PyPI/uv) or run from a Docker container. It has no server component for PR integration (the state service is a separate, opt-in concern).
- **Structured output already exists** — `--output json` on every command, exit codes 0/1/2/3/4, and `strata build plan` already produce everything a PR comment needs. The gap is a recipe showing how to wire it, not new CLI features.

## Considered Options

### Option A — Build a GitHub App (webhook receiver, bot identity, managed permissions)

A registered GitHub App with its own webhook endpoint, private key, and bot user. It would listen for `pull_request` events, run strata, and post comments/checks under its own identity.

**Rejected:**

- Requires hosting a webhook server (infrastructure to operate, another thing to secure)
- Centralises pipeline logic that belongs to the team owning the repo
- Doesn't work for GitHub Enterprise Server without a separate app registration per GHE instance
- Competes with the CI system the team already has — why would they want two places running strata?
- Credential blast radius: the App's private key has broad org-level permissions

### Option B — Reusable GitHub Actions workflow + exit-code contract (chosen)

Publish a callable workflow (`strata-validate-pr.yml`) that teams reference from their own repos. The workflow runs strata commands, interprets exit codes, and optionally posts PR comments using `gh pr comment`. Teams can copy, fork, or ignore it — strata's CLI contract (exit codes + JSON output) is the real interface, the workflow is just a recipe.

**Chosen because:**

- Zero infrastructure to operate
- Works on github.com and GitHub Enterprise Server (uses `GITHUB_API_URL` / `gh` CLI which respects it)
- Pipeline owner keeps full control: they decide which commands run, when, whether to block merge or just warn
- strata stays a CLI tool, not a platform

### Option C — CLI commands that directly call the GitHub API (`strata pr comment`, etc.)

Add `strata pr comment --pr 123 --body "..."` commands that call the GitHub REST API.

**Rejected:**

- `gh pr comment` already does this perfectly — why reimplement it?
- Adds `PyGithub` or `httpx` as a dependency for something that's a one-liner in the workflow
- Couples strata's release cadence to GitHub API changes

## Decision Outcome

Chosen: **Option B — reusable workflow + exit-code contract.**

strata's contribution to PR integration is:

1. **Clear exit codes** — already implemented (exit 3 = validation failure, exit 0 = success)
2. **Structured JSON output** — already implemented (`--output json` on every command)
3. **A reusable workflow template** — a `.github/workflows/strata-validate-pr.yml` that teams `uses:` from their repos
4. **Documentation** — showing how to wire exit codes to PR checks, `gh pr comment` with plan output, and optional merge-blocking

strata does NOT:
- Own the pipeline schedule (teams decide: on every push? only on label? only on `/strata validate` comment?)
- Decide what blocks merge (teams configure branch protection rules themselves)
- Post comments under its own bot identity (uses the workflow's `GITHUB_TOKEN` via `gh`)
- Auto-deploy on merge (teams wire that themselves if they want it)

### Exit-code-to-PR-status mapping

| strata exit code | Meaning                     | GitHub Actions step result | PR check suggestion        |
| ---------------- | --------------------------- | -------------------------- | -------------------------- |
| `0`              | Success                     | ✅ step passes              | Check passes               |
| `1`              | System error                | ❌ step fails               | Check fails (infra issue)  |
| `2`              | Usage error (bad arguments) | ❌ step fails               | Check fails (workflow bug) |
| `3`              | Validation failure          | ❌ step fails               | Check fails (block merge)  |
| `4`              | Lock conflict               | ❌ step fails               | Check fails (retry later)  |

Teams can choose to make exit 3 a **warning** (comment only, don't block) or a **failure** (required check, blocks merge) — that's a branch protection rule decision, not a strata decision.

### Reusable workflow design

```yaml
# .github/workflows/strata-validate-pr.yml (published by strata, called by consumer repos)
name: strata PR validation

on:
  workflow_call:
    inputs:
      deployment-file:
        description: 'Path to the deployment YAML file'
        required: true
        type: string
      strata-version:
        description: 'strata version to install (default: latest)'
        required: false
        type: string
        default: 'latest'
      python-version:
        description: 'Python version'
        required: false
        type: string
        default: '3.13'
      post-comment:
        description: 'Post validation results as a PR comment'
        required: false
        type: boolean
        default: true
      run-plan:
        description: 'Run build plan (requires Terraform)'
        required: false
        type: boolean
        default: false
    secrets:
      STRATA_SECRETS:
        description: 'Optional secrets for deep validation'
        required: false

jobs:
  validate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write    # for gh pr comment
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ inputs.python-version }}

      - name: Install strata
        run: |
          pip install uv
          uv pip install --system "xyz-strata${{ inputs.strata-version != 'latest' && format('=={0}', inputs.strata-version) || '' }}"

      - name: Validate
        id: validate
        run: |
          set +e
          output=$(strata validate -f "${{ inputs.deployment-file }}" --deep --output json 2>&1)
          exit_code=$?
          echo "exit_code=$exit_code" >> $GITHUB_OUTPUT
          echo "output<<EOF" >> $GITHUB_OUTPUT
          echo "$output" >> $GITHUB_OUTPUT
          echo "EOF" >> $GITHUB_OUTPUT
          exit $exit_code
        continue-on-error: true

      - name: Build plan
        if: inputs.run-plan && steps.validate.outputs.exit_code == '0'
        id: plan
        run: |
          set +e
          output=$(strata build plan -f "${{ inputs.deployment-file }}" --output json 2>&1)
          exit_code=$?
          echo "exit_code=$exit_code" >> $GITHUB_OUTPUT
          echo "output<<EOF" >> $GITHUB_OUTPUT
          echo "$output" >> $GITHUB_OUTPUT
          echo "EOF" >> $GITHUB_OUTPUT
          exit $exit_code
        continue-on-error: true

      - name: Post PR comment
        if: inputs.post-comment && github.event_name == 'pull_request'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          validate_code="${{ steps.validate.outputs.exit_code }}"
          if [ "$validate_code" = "0" ]; then
            icon="✅"
            status="passed"
          else
            icon="❌"
            status="failed (exit $validate_code)"
          fi

          body="### ${icon} strata validation ${status}\n\n"
          body+="**File:** \`${{ inputs.deployment-file }}\`\n\n"

          if [ "$validate_code" != "0" ]; then
            body+="\`\`\`json\n${{ steps.validate.outputs.output }}\n\`\`\`\n"
          fi

          if [ -n "${{ steps.plan.outputs.output }}" ]; then
            body+="\n<details><summary>Build plan</summary>\n\n"
            body+="\`\`\`json\n${{ steps.plan.outputs.output }}\n\`\`\`\n"
            body+="</details>\n"
          fi

          echo -e "$body" | gh pr comment ${{ github.event.pull_request.number }} --body-file -

      - name: Final status
        if: steps.validate.outputs.exit_code != '0'
        run: exit 1
```

### Consumer repo usage

```yaml
# In the consumer's own .github/workflows/pr.yaml
name: PR checks
on:
  pull_request:
    paths:
      - 'deploy/**'
      - 'config/**'
      - 'environments/**'

jobs:
  strata:
    uses: huybrechtsxyz/strata/.github/workflows/strata-validate-pr.yml@main
    with:
      deployment-file: deploy/deploy-prd.yaml
      post-comment: true
      run-plan: true
```

Or, for teams that want full control, they skip the reusable workflow entirely and just run strata directly:

```yaml
# Minimal — just use exit codes
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install xyz-strata
      - run: strata validate -f deploy/deploy-prd.yaml --deep
      # Exit 3 = step fails = check fails = merge blocked (if required check)
```

### GitHub Enterprise Server support

The reusable workflow and all examples work on GitHub Enterprise Server without modification because:

- `gh` CLI respects `GITHUB_API_URL` (set automatically by the GHE runner)
- `actions/checkout@v4`, `actions/setup-python@v5` work on GHE (mirrored actions)
- No GitHub App registration, no github.com-specific API calls
- PyPI access is required (or a private PyPI mirror / pre-built Docker image)

For air-gapped GHE environments without PyPI access, use the Docker approach:

```yaml
jobs:
  validate:
    runs-on: self-hosted
    container: ghcr.io/huybrechtsxyz/strata-cli:latest  # or internal registry
    steps:
      - uses: actions/checkout@v4
      - run: strata validate -f deploy/deploy-prd.yaml --deep
```

### What strata does NOT do (and why)

| Concern                      | Owner          | Why not strata                                                    |
| ---------------------------- | -------------- | ----------------------------------------------------------------- |
| When to run validation       | Pipeline owner | Teams have different trigger policies (every push, label, manual) |
| Whether to block merge       | Branch rules   | Some teams want advisory-only; others want hard gates             |
| Auto-deploy on merge         | Pipeline owner | Many teams have separate deploy pipelines / environments / rings  |
| Bot identity for PR comments | GitHub App     | Not worth the operational cost for a comment — use `GITHUB_TOKEN` |
| Webhook event processing     | GitHub App     | Requires hosting a server — contradicts strata's CLI-first design |
| Multi-repo checkout in CI    | Pipeline owner | Only they know which repos are needed and how to authenticate     |

### Consequences

**Good:**
- Zero new strata features needed for basic PR integration (exit codes + JSON already work)
- Works on github.com and GitHub Enterprise Server identically
- Pipeline owner keeps full control — strata doesn't impose workflow opinions
- No infrastructure to operate (no webhook server, no bot, no database)
- Teams can adopt incrementally: start with just exit codes, add comments later, add plan later

**Bad:**
- No unified "strata bot" identity on PRs (comments come from the workflow's `github-actions[bot]`)
- No server-side event processing (can't react to PR events without a running workflow)
- Teams must write/maintain their own workflow (or adopt the reusable one)
- No auto-discovery of which deployment files are affected by a PR (teams must specify)

**Neutral:**
- If demand for a GitHub App emerges later (e.g., comment-triggered commands like `/strata plan`), it can be built on top without changing anything here — the CLI contract is the stable interface either way

## Open Questions (resolved)

| #   | Original question                 | Resolution                                                                                                  |
| --- | --------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1   | GitHub App or Actions?            | Actions. No App.                                                                                            |
| 2   | Run on every update or on demand? | Pipeline owner decides — not our call.                                                                      |
| 3   | Block merge or just comment?      | Pipeline owner decides via branch protection. Exit 3 = failure; they choose whether that check is required. |
| 4   | Support merge strategies?         | Not relevant — strata doesn't interact with merge at all.                                                   |
| 5   | Auto-deploy on merge?             | Pipeline owner wires that if they want it.                                                                  |
| 6   | Rollback on failure?              | Pipeline owner's problem.                                                                                   |
| 7   | Scope to changed files only?      | Pipeline owner uses `paths:` filter in their workflow trigger.                                              |
| 8   | Multi-repo?                       | Pipeline owner handles checkout.                                                                            |
| 9   | GitHub Enterprise?                | Yes — `gh` CLI + `GITHUB_API_URL` works everywhere. Docker image for air-gapped.                            |
| 10  | Branch protection rules?          | Not our concern — teams configure these themselves.                                                         |
