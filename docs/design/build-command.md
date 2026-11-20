# `strata build run` Command — Design

- Status: current
- Last updated: 2026-11-20

## Overview

`strata build run DEPLOYMENT` turns a deployment into files on disk: each provisioner's source, its tool input
(Terraform's `strata.auto.tfvars.json`, Helm values, Compose files, a GitOps step's rendered file), and the records a
deploy and an audit read later. It **renders only**: it never calls `plan`, `deploy` or `destroy`
([ADR-0022](../decisions/0022-strata-build-run.md) D4), and it never resolves a secret. A later `deploy run` executes
exactly what the build wrote; the build output can be carried to a deploy in another job or on another agent.

The command ([build_command.py](../../src/strata/commands/build_command.py)) is thin glue over
`build_controller.build_run()` ([build_controller.py](../../src/strata/controllers/build_controller.py)):
`open_solution(path).require_valid()`, then the controller, with `command_run()` for the lifecycle.

```
strata build run DEPLOYMENT [--path PATH] [--build-path PATH] [--clean/--no-clean] [--dry-run] [--resolve]
                            [--offline] [--env-file PATH]... [--pin VERSION]...
```

## What a build does, in order

1. **Inputs.** `--env-file` values are added to the process environment (an exported variable wins). The deployment
   is resolved through `extends` and tenant defaults (with `--pin` overriding `spec.version` for this run only), then
   its workspace, reachable environments, merged `properties`/`custom`, tenant and version documents.
2. **Integration preflight.** Every enabled Integration is checked offline; a required one that is not ready stops
   the build before anything changes ([integration-readiness.md](integration-readiness.md)).
3. **`--resolve`** (opt-in) resolves every declared variable, feature and secret, including integration-backed
   stores, and reports what fails. The values are never written.
4. **Clean.** The build folder is wiped (see "Cleaning").
5. **The resolved graph.** `build_resolved_workspace_graph()` assembles every document the workspace reaches, with
   the build-time values ([build-time-value-categories.md](build-time-value-categories.md)). `resolved.yaml` records
   it, with the version pins applied and which commits each copy came from
   ([remote-commit-provenance.md](remote-commit-provenance.md)).
6. **Assets** each execution step selects are materialised once, by slot ([asset-management.md](asset-management.md)).
7. **Provisioner steps,** in `depends_on` order: a provisioner's own `depends_on` sources first
   ([provisioner-source-dependencies.md](provisioner-source-dependencies.md)), then its source is copied
   byte-for-byte (`sync_source()`), then the integration's `prepare()` writes its input. Terraform gets one
   `strata` object and a matching `strata_variables.tf` ([unified-terraform-projection.md](unified-terraform-projection.md));
   an `output.template` is validated here and rendered at deploy ([value-token-resolution.md](value-token-resolution.md)).
8. **Workload modules** (Helm, Compose), from each namespace's `modules`: a second, separate input shape
   ([workload-pipeline.md](workload-pipeline.md)). A module reference with `enabled: false` is skipped before it is
   even resolved, as a disabled workspace resource is.
9. **Report estimate.** With an `output_store`, the size of the report record `deploy run` will write is estimated
   ([deployment-documentation.md](deployment-documentation.md)); a failure is only a warning.
10. **SBOM and policies,** last, over what was rendered: `sbom.json` ([sbom-generation.md](sbom-generation.md)), then
    `image_tag_policy`, `cve_policy` and `checkov_policy`, whose results go to `policy_results.json`
    ([audit-trail.md](audit-trail.md), "Policy results").

Value tokens are not resolved at build time. Everything a value fills in (backend settings, `dns`/`networks` values,
templates) is resolved at deploy, where secrets are available and never written to disk
([value-token-resolution.md](value-token-resolution.md)).

### Cleaning

The default build folder, `<root>/build/<deployment>` (`layout.build_dir()`, ignored by discovery), belongs to the
build alone, so it is always wiped first: otherwise a removed document's old output would survive into the next
apply. A `--build-path` may point somewhere the caller does not own (a CI artifact staging folder), so it is wiped only
with `--clean`. A wipe that fails is a `BuildCleanError`.

### `--dry-run`

Not a second, in-memory render. Every validating step runs for real (deployment, workspace, values, integration
resolution, and `--resolve`), so a dry run catches what a real one would; only the side effects are skipped: the
wipe, copying sources and assets, writing `resolved.yaml`, rendering, and the SBOM and policies, which need rendered
output. Each skipped action is reported as "would ..." through the same progress callback a real run uses.

## Decisions on v1 build behaviour

A comparison of v1's `RunBuildCommand` and its seven builders with `build_run()` found that most of what v2 left
out was cut on purpose in ADR-0020, ADR-0022 and ADR-0023. What fell between them was settled as follows:

| v1 behaviour                                          | v2                                                                                                                 |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Wipe the build folder first                           | Built (see "Cleaning").                                                                                            |
| `ModuleReferenceModel.enabled`                        | Honoured by the workload pipeline.                                                                                 |
| Render in memory for a dry run                        | `--dry-run` as above, through the real code path.                                                                  |
| Jinja2-render every copied `.tf`/`.tfvars`/playbook   | Not ported: strata supplies input to IaC, it does not rewrite its source ([ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)). |
| SBOM on every build                                   | Built ([sbom-generation.md](sbom-generation.md)); ADR-0022's cut was based on a misreading of v1.                    |
| Per-category tfvars files and `OutputProfileModel.emits` | Replaced by one `strata` variable and a generated declaration ([ADR-0035](../decisions/0035-unified-terraform-projection.md)); nothing left to gate. |
| Validating input against `variables.tf`               | The shape check is no longer needed: strata writes the `variable "strata"` declaration that matches its payload. Checking the keys a root reads is not covered (below). |
| A `.gitignore` in the build output                    | v1 never wrote one; v1's solution scaffold ignored `build/`. It belongs to the solution's own `.gitignore` ([solution-scaffolding.md](solution-scaffolding.md)), not to the build. |
| Build lifecycle hooks, `phase: build` policies        | Not a build concern: v2 runs lifecycle hooks for no command yet, and policies are ported per command (ADR-0020).   |

## Not covered

- **Cross-deployment overlap detection** (v1's `overlap_controller.py`): two deployments claiming the same Terraform
  state backend or the same namespace. It was deferred until a build layer existed to key on; it does now, but no
  real collision has been reported. `validate` does warn about the narrower case of strata writing into a folder the
  build reads from (`audit_sink_overlaps_source`, `gitops_output_overlaps_source`).
- **Checking the keys a Terraform root reads** (gap #15 in `gap_fit_v1.md`). v1 checked each declared variable and
  feature against the root's `variables.tf`. With one `strata` variable, `variables` and `flags` are maps: a key the
  root reads but no environment declares fails at `terraform plan`, and a key nothing reads is ignored. Catching the
  first at build time would mean reading the root's HCL for `var.strata.variables[...]` references.
- **`modules` on a topology component** (code attached to one resource). No real workspace uses it; the case that
  prompted it, a Function App's code, is a container image supplied through `store: artifact`
  ([ADR-0026](../decisions/0026-pinnable-artifact-references.md)).
- **`oci` and `helm` remote fetches** for a provisioner source: see [remotes.md](remotes.md), "Not covered".

## Related Decisions

- [ADR-0022](../decisions/0022-strata-build-run.md) — the orchestration (D1-D7).
- [ADR-0023](../decisions/0023-build-output-rendering.md) — what gets rendered.
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) — sources are copied, never rewritten.
- [ADR-0029](../decisions/0029-provisioner-source-dependencies.md) — materialising a provisioner only another
  provisioner depends on.
- [ADR-0035](../decisions/0035-unified-terraform-projection.md) — one `strata` variable for Terraform.
- [validate-command.md](validate-command.md) — the precondition `build run` requires.

## History

- The command was built bottom-up in September 2026: remote resolution, `sync_source()` (each source copied to its
  own `source_path` under the build folder, which is what lets sibling Terraform modules find each other by relative
  path), `resolve_integration()`, the resolved graph, the ordered step loop, then the CLI, Helm and Compose.
- `build_time_keys()` was replaced by `build_value_references()`, which walks the same reachable environments without
  touching the network.
- The v1 comparison above listed five unrecorded gaps. Cleaning and `enabled` were fixed; the `.gitignore` claim was
  retracted after no v1 builder was found to write one; source rewriting became ADR-0025; hooks and build policies
  were found to belong to other commands.
- `OutputProfileModel` was the largest known risk against a real consumer (`config-repo` used
  `format: custom, emits: [...]`). It was first blocked on categories v2 did not emit, then made unnecessary by
  ADR-0035.
- The doc stayed a work doc long after the command was done, listing deploy-time work as "blocked on `deploy run`".
  Once that work was done, `OutputProfileModel` archived and the last two items (overlap detection, `variables.tf`
  keys) placed under "Not covered", nothing was in progress and it moved to design.
