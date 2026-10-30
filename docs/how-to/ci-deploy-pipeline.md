# How To: Run strata in a CI pipeline

A deploy pipeline is four commands run in order. Each one stops the pipeline with a non-zero exit
code, so the workflow itself needs no logic beyond running them. This guide shows the commands, a
complete GitHub Actions workflow, a variant that splits plan from apply behind an approval, and what
each exit code means. The same commands work on any CI system; only the install and the secret
export lines change.

```
strata validate  ->  strata build run D  ->  strata policy check D  ->  strata deploy run D
   offline            renders, no network       optional gate              executes tools
```

`D` is the `meta.name` of a `kind: deployment` document. `strata deploy list` lists them; a
deployment marked `(partial)` is a base that others extend and cannot be deployed. To build a CI matrix, ask for
the deployable ones of one workspace and ring: `strata deploy list --workspace W --ring R --deployable -o json`
returns `data.deployments[]` (`name`, `tenant`, `version`, `ring`, `order`, ...). A filter that names nothing
exits 2 instead of returning an empty list.

## 1. The four steps

| Step                    | What it does                                                                                                    | Needs                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `strata validate`       | Checks every document and every reference between them. `--strict` also fails on warnings, such as a stale pin. | Nothing: offline.                                       |
| `strata build run D`    | Renders the Terraform, Helm and Compose artifacts into `build/D`. Executes nothing.                             | Credentials to clone private sources. Scanners (below). |
| `strata policy check D` | Evaluates the workspace's policies now and records the results. Optional (see below).                           | Scanners, and gate credentials if a gate policy is set. |
| `strata deploy run D`   | Executes the rendered output: `terraform plan` and `apply`, or the Helm and Compose equivalent.                 | The tools on `PATH`, and every store credential.        |

Three things worth knowing:

- **`build run` does not resolve secrets** unless you pass `--resolve`. It only reads `constant` and
  `environment` values, so a build job needs almost no credentials. `--resolve` is a full pre-deploy
  smoke test: it tries every declared value, including Key Vault and Infisical, and reports the
  ones that fail, without writing any of them down.
- **`build run` already evaluates `cve_policy` and `checkov_policy`**, so Trivy or Grype, and Checkov,
  must be on `PATH` before the build step if the workspace configures them.
- **`deploy run` already runs `release_gate_policy` and `image_gate_policy`**, after values resolve and
  before any provisioner. A separate `policy check` step is only worth having when you want the
  gate as its own step or job, for example before an approval, or in a pull request check that never
  installs Terraform. It exits 2 when the workspace configures none of the four policies, so leave
  the step out if there are none. See [security-gates.md](security-gates.md).

`strata deploy run --force` is accepted for compatibility with v1 pipelines and currently does
nothing. `--dry-run` runs init, validate and plan and stops before any change; the security gates
still run. `--smoke-test` reports which steps would run without contacting any tool, so it needs no
credentials.

## 2. Install strata

strata needs Python 3.13. While v2 is in release-candidate, **pin the version**: an unpinned
`pip install xyz-strata` still resolves to the last v1 release.

```yaml
- uses: astral-sh/setup-uv@v8.1.0
- run: uv tool install "xyz-strata==2.0.0rc2"
```

`setup-uv` puts `~/.local/bin` on `PATH`, so `strata` is available to the following steps. If your
runner reaches PyPI through a corporate mirror, add `UV_INDEX_STRATEGY: unsafe-best-match` to the
step's `env`.

A container image is published to `ghcr.io/huybrechtsxyz/strata`. It holds strata only, not Terraform
or Helm, and pre-release tags never move `latest`.

If your pipeline installs packages through Aikido Safe Chain, a freshly released strata is held back
for 48 hours; see [aikido-safe-chain.md](aikido-safe-chain.md).

## 3. Give the pipeline its credentials

strata reads credentials from the process environment, so the workflow only has to export the CI
secrets that each store needs. Which step needs which:

| Credential                                           | Read by                              | Example environment variables                                                                                           |
| ---------------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| An `environment` store value (including a git token) | `build run`, `deploy run`            | the variable named in the store's `value:`, e.g. `CUSTOMER_REPOS_PAT`                                                   |
| Azure Key Vault or App Configuration                 | `deploy run`, `build run --resolve`  | `AZURE_KEYVAULT_URL`, plus Azure auth ([azure-keyvault.md](../integrations/azure-keyvault.md))                          |
| Infisical                                            | `deploy run`, `build run --resolve`  | `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID` ([infisical.md](../integrations/infisical.md)) |
| A security gate (Aikido)                             | `deploy run`, `policy check`         | `AIKIDO_CI_TOKEN`, `AIKIDO_CLIENT_ID`, `AIKIDO_CLIENT_SECRET`, or Environment keys                                      |
| A tool's own login, e.g. a Terraform Cloud token     | the tool itself, during `deploy run` | `TF_TOKEN_app_terraform_io`                                                                                             |

strata resolves the values your Environment documents declare and passes them to the tool. Do not
wire `TF_VAR_*` variables by hand. A secret can also live in a store strata resolves, so the
workflow only needs the credential for that store. Give each step only what it reads: the build step
usually needs nothing beyond a git token.

## 4. A complete workflow

One job, run by hand, with a dry-run switch. The `environment:` line lets you scope secrets and add
required reviewers in the repository settings.

```yaml
name: Deploy

on:
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Plan only, make no change"
        type: boolean
        default: true

permissions:
  contents: read

env:
  DEPLOYMENT: prd-deployment      # meta.name of the kind: deployment document
  STRATA_VERSION: "2.0.0rc2"

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v7

      - uses: astral-sh/setup-uv@v8.1.0
      - name: Install strata
        run: uv tool install "xyz-strata==${STRATA_VERSION}"

      - uses: hashicorp/setup-terraform@v4
        with:
          terraform_wrapper: false   # the wrapper adds output around Terraform's stdout, which strata parses

      - name: Validate the solution
        run: strata validate

      # Offline: is every tool this deployment uses installed, and at the version it asks for?
      - name: Check the tools
        run: strata integration list --deployment "$DEPLOYMENT"

      - name: Render
        env:
          CUSTOMER_REPOS_PAT: ${{ secrets.CUSTOMER_REPOS_PAT }}   # only if a source is private
        run: strata build run "$DEPLOYMENT"

      # Remove this step if the workspace configures no policy: it exits 2 with nothing to check.
      - name: Check policies
        env:
          AIKIDO_CI_TOKEN: ${{ secrets.AIKIDO_CI_TOKEN }}
        run: strata policy check "$DEPLOYMENT"

      - name: Deploy
        env:
          DRY_RUN: ${{ inputs.dry_run }}
          INFISICAL_CLIENT_ID: ${{ secrets.INFISICAL_CLIENT_ID }}
          INFISICAL_CLIENT_SECRET: ${{ secrets.INFISICAL_CLIENT_SECRET }}
          INFISICAL_PROJECT_ID: ${{ vars.INFISICAL_PROJECT_ID }}
          AIKIDO_CI_TOKEN: ${{ secrets.AIKIDO_CI_TOKEN }}
        run: |
          args=""
          if [ "$DRY_RUN" = "true" ]; then args="--dry-run"; fi
          strata deploy run "$DEPLOYMENT" $args
```

Notes on the workflow:

- Run strata from the repository root, where `strata.yaml` is. To run from elsewhere, pass `--path`
  (`validate` takes the path as an argument instead) or set `STRATA_WORK_PATH`.
- Add `--strict` to `strata validate` when a warning, such as a stale version pin, should block a
  release.
- If you use a `kind: version` document to pin images and charts, pass the same `--pin NAME` to
  `build run`, `policy check` and `deploy run`, so the deploy executes exactly the build you
  rendered.
- A release gate judges the commit being deployed and reads it from the CI variables (`GITHUB_SHA`,
  `BUILD_SOURCEVERSION`, `CI_COMMIT_SHA`, and others). Check out the commit you mean to deploy.
- To limit a workflow to part of a deployment, use `--scope LABEL` (matches the `scope:` of an
  execution step, for example `infra` or `apps`) or `--stage NAME`. A workflow that needs no
  kubeconfig can then never touch the Helm steps.
- `--follow` streams the tools' output live; see [deploy-run-streaming.md](deploy-run-streaming.md).
- To copy the deployment's images to a registry before `deploy run` (for example from a build feed to the
  registry the apps pull from), list them with `strata deploy images "$DEPLOYMENT" -o json`. Each entry in
  `data.images[]` has `image_name`, `image_tag`, `image_ref` and `registry`, with any version pin applied.
  It needs no build and reads no secret, and takes `--pin` like the other commands. An image with no tag
  is reported as a warning.

## 5. Plan in one job, apply in another

To review the plan before anything changes, split the deploy with `deploy plan` and `deploy apply`.
`plan` saves a `<step>.tfplan` file inside the build directory. `apply` applies exactly that file,
re-running `init` first, and skips validate and plan. Put the apply job behind a protected
environment so a person approves it.

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      # checkout, install strata, validate, build run, as in section 4
      - uses: actions/upload-artifact@v7
        with:
          name: build
          path: build/

  plan:
    needs: build
    runs-on: ubuntu-latest
    steps:
      # checkout, install strata and Terraform
      - uses: actions/download-artifact@v8
        with:
          name: build
          path: build/
      - run: strata deploy plan "$DEPLOYMENT"      # shows the diff, saves <step>.tfplan
      - uses: actions/upload-artifact@v7
        with:
          name: planned
          path: build/

  apply:
    needs: plan
    runs-on: ubuntu-latest
    environment: production-approval               # required reviewers
    steps:
      # checkout, install strata and Terraform
      - uses: actions/download-artifact@v8
        with:
          name: planned
          path: build/
      - run: strata deploy apply "$DEPLOYMENT"
```

Each job still needs its own checkout, strata and Terraform, and the credentials from section 3.
Things to know:

- A saved plan is only valid with the backend configuration and provider lock file it was made
  with. That is Terraform's rule. If `apply` reports a provider or lock-file mismatch, commit
  `.terraform.lock.hcl` with your Terraform source. `upload-artifact` skips files whose names start
  with a dot unless you set `include-hidden-files: true`.
- A step that reads another step's output with `${output:...}` may show "(known after apply)" in the
  plan. Apply dependent steps in dependency order.
- `deploy apply` records the audit trail like `deploy run`, including the `--change-*` flags.
- `deploy setup` and `deploy check` run only init, or init and validate, if you want those as
  separate stages.

## 6. Exit codes

Every command uses the same four codes, so a pipeline can react without parsing output.

| Code | Meaning                                                                                                                                  | What to do                           |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| 0    | Success. A policy under `enforcement: warn` that is breached still exits 0.                                                              | Continue.                            |
| 1    | System failure: a source or remote could not be fetched, or a path could not be cleaned.                                                 | A retry can help.                    |
| 2    | The pipeline itself is wrong: not inside a solution, unknown deployment, a bad flag or `--pin`, or `policy check` with nothing to check. | Fix the workflow.                    |
| 3    | The content failed: a validation error, a value that did not resolve, a gate breach, a required tool missing or too old, a failed step.  | Fix the cause. Do not retry blindly. |

Add `--output json` (or set `STRATA_OUTPUT=json`) when a later step parses the result. It writes
exactly one JSON document to stdout, on success and on failure, and `--follow` output moves to
stderr. The document carries `ok`, a `summary`, and every diagnostic with its `code`, so a failure
can be turned into an annotation. The exit code remains the contract.
