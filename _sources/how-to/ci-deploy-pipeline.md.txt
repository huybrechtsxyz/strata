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

Five things worth knowing:

- **`build run` does not resolve secrets** unless you pass `--resolve`. It only reads `constant` and
  `environment` values, so a build job needs almost no credentials. `--resolve` is a full pre-deploy
  smoke test: it tries every declared value, including Key Vault and Infisical, and reports the
  ones that fail, without writing any of them down.
- **`build run` already evaluates `cve_policy` and `checkov_policy`**, so Trivy or Grype, and Checkov,
  must be on `PATH` before the build step if the workspace configures them.
- **`build run` pins registry Helm charts**, so it needs `helm` on `PATH` and the registries'
  credentials when the workspace has any. Without them the build still succeeds, but leaves those
  charts unpinned, and the deploy stops at them. Keeping `.strata/helm/` from the build job lets the
  deploy job skip the registry ([chart-registries.md](../integrations/chart-registries.md)).
- **`deploy run` already runs `release_gate_policy` and `image_gate_policy`**, after values resolve and
  before any provisioner. A separate `policy check` step is only worth having when you want the
  gate as its own step or job, for example before an approval, or in a pull request check that never
  installs Terraform. It exits 2 when the workspace configures none of its policies, so leave
  the step out if there are none. See [security-gates.md](security-gates.md).
- **`build run` and `deploy run` also judge image tags** when the workspace sets `image_tag_policy`: an image
  with no tag, a floating tag such as `latest`, or a tag that does not match the shape you require fails the
  step (`enforcement: deny`) or warns (`warn`). It is offline and needs no credentials. `build run` judges it
  at the end, after the SBOM, so it also judges the images the charts run (`sbom.render_charts`, on by default;
  the build agent needs `helm` for that, else each chart is an `info` line and skipped). `deploy run`
  judges it again before any value is resolved, so pass the same `--pin` to both. To fail fast, before the build
  clones and renders, run `strata deploy images "$DEPLOYMENT"` first: it judges the declared images in seconds.
  See "Image tag policy" in [the workspace reference](../config/workspace.md).

`strata deploy run --force` is accepted for compatibility with v1 pipelines and currently does
nothing. `--dry-run` runs init, validate and plan and stops before any change; the security gates
still run. `--smoke-test` reports which steps would run without contacting any tool, so it needs no
credentials.

## 2. Install strata

strata needs Python 3.12 or later (tested on 3.12, 3.13 and 3.14). While v2 is in release-candidate, **pin the version**: an unpinned
`pip install xyz-strata` still resolves to the last v1 release.

```yaml
- uses: astral-sh/setup-uv@v8.1.0
- run: uv tool install "xyz-strata==2.0.0rc2"
```

`setup-uv` puts `~/.local/bin` on `PATH`, so `strata` is available to the following steps. If your
runner reaches PyPI through a corporate mirror, add `UV_INDEX_STRATEGY: unsafe-best-match` to the
step's `env`.

A container image is published to `ghcr.io/huybrechtsxyz/strata` in two variants; pre-release tags never move
`latest`:

- **`strata:<version>`** (also `-slim`, and `latest`): strata and the tools strata itself calls (`git`, and `ssh`
  for `ssh_key` auth). Enough for `validate`, `schema`, `deploy steps` and a build without Helm modules.
- **`strata:<version>-full`** (and `latest-full`): adds OpenTofu, Helm 4, the Docker CLI with Compose, Trivy and
  Checkov.
  Use it for a workspace with Helm modules: `build run` itself calls Helm (it pins registry charts and renders
  charts for the SBOM).

Neither has Terraform: its licence is not settled for a public image. The full image runs root modules with
OpenTofu instead (`tool: opentofu`, see [workspace.md](../config/workspace.md#opentofu)). Mount the checkout and work in it:

```bash
docker run --rm -v "$PWD:/work" -w /work ghcr.io/huybrechtsxyz/strata:<version> validate config
```

For Terraform, or another version of a tool, build your own image on either variant;
[cli-image.md](../design/cli-image.md) has the example, the Docker socket mount for Compose steps, and what each
variant holds and why.

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
  execution step, for example `infra` or `apps`) or `--stage NAME`. Both can be repeated
  (`--scope infra --scope network`). A workflow that needs no kubeconfig can then never touch the
  Helm steps. `strata deploy steps D` lists the steps and scopes; see
  [Plan jobs from the step list](#plan-jobs-from-the-step-list).
- `--follow` streams the tools' output live; see [deploy-run-streaming.md](deploy-run-streaming.md).
- To copy the deployment's images to a registry before `deploy run` (for example from a build feed to the
  registry the apps pull from), list them with `strata deploy images "$DEPLOYMENT" -o json`. Each entry in
  `data.images[]` has `image_name`, `image_tag`, `image_ref` and `registry`, with any version pin applied.
  It needs no build and reads no secret, and takes `--pin` like the other commands. An image with no tag
  is reported as a warning. When the workspace sets `image_tag_policy`, the policy judges every image instead
  and a `deny` breach exits 3, so the copy step fails before it moves an unpinned or floating image.

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
- `deploy apply` only works for tools that save a plan. A `script` or ArgoCD/Flux step is refused before anything
  runs; use `deploy run` for those, or leave them out with `--stage` / `--scope`.
- A `script` step runs in a preview (`--dry-run`, `deploy plan`) only when its provisioner sets `plan_args`;
  otherwise the preview prints `would run: ...`. The script sees `STRATA_PHASE` (`plan`, `apply` or `destroy`), so
  guard anything that changes state with it. A preview that executes the script streams its output with `--follow`.
- A `script` step receives every resolved value in `STRATA_VALUES` unless its provisioner lists `values`; then it
  gets only those keys (plus `deployment:name` and `deployment:tenant`), so an unrelated secret never reaches it.
- A `script` step passes values to the steps after it by writing a JSON object to the file named by
  `STRATA_OUTPUT_FILE`; they become `${output:<step>.<key>}`. List secret keys in `"_sensitive"` so they are used in
  the run but never recorded in the `output_store`.
- `deploy setup` and `deploy check` run only init, or init and validate, if you want those as
  separate stages.

### Plan jobs from the step list

`strata deploy steps D -o json` lists the steps `deploy run D` would run, in its order, without
running anything or needing a build or credentials. Each entry in `data.steps[]` has `name`,
`scope`, `order`, `level` (steps on one level do not depend on each other), `depends_on`, the
`integration` it needs, and `capabilities`: `saved_plan` is false for a step `deploy apply` cannot
take (a `script` or GitOps step; run it with `deploy run`), `destroy` false for one `deploy destroy`
cannot tear down. `data.scopes[]` gives each scope the scopes it waits for. `--stage` and `--scope`
select exactly as on `deploy run`.

One job per scope, in scope order:

```sh
strata deploy steps "$DEPLOYMENT" -o json | jq -c '[.data.scopes[] | {scope, depends_on}]'
```

Skip a job when the deployment has no step in its scope. An empty selection exits 0, so check the
count:

```sh
n=$(strata deploy steps "$DEPLOYMENT" --scope infra -o json | jq '.data.steps | length')
if [ "$n" -eq 0 ]; then echo "no infra steps"; exit 0; fi
```

Send each step to the command that can run it:

```sh
strata deploy steps "$DEPLOYMENT" -o json | jq -r '.data.steps[]
  | "\(.name) \(if .capabilities.saved_plan then "plan+apply" else "run" end)"'
```

`--check-tools` also checks each listed step's tool on the machine it runs on (on `PATH`, required
version) and exits 3 when one is not ready, so a deploy job can fail before it starts:
`strata deploy steps "$DEPLOYMENT" --scope apps --check-tools -q`. `--destroy` lists the steps in
teardown order.

## 6. Tear a deployment down

`strata deploy destroy D` removes what `deploy run` created: Terraform-managed resources, Helm releases, Compose
stacks and GitOps files, last step first. It cannot be undone, so it needs exactly one of `--dry-run` (preview,
changes nothing), `--smoke-test` (report only) or `--force` (destroy, no prompt). Anything else exits 2. Preview in one job and destroy in a
job behind a protected environment, so a person approves exactly what the preview showed.

```yaml
jobs:
  preview:
    runs-on: ubuntu-latest
    steps:
      # checkout, install strata and Terraform, validate, build run, as in section 4
      - run: strata deploy destroy "$DEPLOYMENT" --dry-run   # streams what would be removed

  destroy:
    needs: preview
    runs-on: ubuntu-latest
    environment: production-destroy                         # required reviewers
    steps:
      # checkout, install strata and Terraform, validate, build run
      - run: |
          strata deploy destroy "$DEPLOYMENT" --force \
            --change-system jira --change-id "$TICKET" --change-reason "decommission"
```

Things to know:

- A destroy reads the build directory like a deploy does: run `build run` first, with the same `--pin`.
- `--smoke-test` calls no tool and reads no store, like `deploy run --smoke-test`: values still resolve, then each
  step is reported as "would destroy step X", last first. It catches a step that cannot be destroyed and a filter
  that selects nothing, but not a missing output record or a state problem; `--dry-run` does.
- `--dry-run` streams its preview without `--follow`. Terraform runs `plan -destroy` (it takes the state lock and
  needs credentials) and never writes `<step>.tfplan`, so a reviewed apply plan is left untouched. Helm lists
  `Kind/name` per resource, never Secret data; Compose lists the stack's running services; GitOps says whether the
  file is in the config repo.
- A destroy does not run `image_tag_policy` or the security gates: a `deny` never blocks a teardown.
- To destroy part of a deployment, use `--stage` or `--scope`. Run `--scope apps` before `--scope infra`. A destroyed
  step that other steps depend on, and that are not in the run, is a warning, not an error, so the second run stays
  possible.
- A `script` step can be destroyed only when its provisioner sets `destroy_args`; `--force` then runs the script with
  them and `STRATA_PHASE=destroy`. Without `destroy_args`, `--force` refuses before anything is removed, and names
  the step; leave it out with `--stage` or `--scope`. Under `--dry-run` it is a warning, or, with `destroy_args`,
  the line `would run: ...`: a destroy preview never executes the script.
- A Terraform step that reads another step's output with `${output:...}` needs the workspace's `output_store`: a
  destroy collects no outputs, so it reads the ones `deploy run` recorded. Without a usable record the run stops
  before destroying anything. Pass `--output-max-age` when the record is stale. A successful destroy clears the
  destroyed steps' records.
- Re-running after a partial failure is safe: a Helm release that is already gone counts as removed.
- A real destroy records the audit trail with `action: destroy`, including the `--change-*` flags, and `strata audit
  status` shows it. A sink receives `deployment.destroyed` rather than `deployment.completed`, so list that event
  type in the sink's `events`. A dry run records nothing.

## 7. Exit codes

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
