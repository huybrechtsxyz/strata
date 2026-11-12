# `strata deploy steps` — Work

- Status: draft (design complete, not built)
- Last updated: 2026-11-12

## Overview

A CI pipeline can already run part of a deployment: `--stage NAME` and `--scope LABEL` on
`deploy setup|check|plan|apply|run|destroy` limit a run to one execution step or to the steps with
one `scope:` label ([ci-deploy-pipeline.md](../how-to/ci-deploy-pipeline.md)). It cannot ask strata
which steps and scopes a deployment has, in which order they run, which tool each needs, or which
of them can go through a `plan` -> `apply` split. Pipelines hardcode the names today, or parse the
workspace YAML themselves, which goes wrong as soon as a deployment's workspace comes through
`extends` or a step is added.

`strata deploy steps DEPLOYMENT` lists a deployment's execution steps exactly as `deploy run` would
select and order them, so a pipeline can build its jobs, matrix or approval gates from `-o json`.
It is the step-level counterpart of `strata deploy list`, which already serves as the
deployment-level CI matrix. With it, `--stage` and `--scope` become repeatable on every deploy
command, through one shared selection function, so what `deploy steps` lists is what the run
commands run.

This stays a work doc until it is built; it then moves to `docs/design/` with a `## History`.

## Current Design / Progress

Design complete, nothing built. The implementation plan is below.

### What exists today

Verified in the code (2026-11-12):

- **No command lists steps.** `deploy list` stops at the deployment (workspace, tenant, version,
  ring, `order`, `wave`, environments). `deploy report -o json` has `data.steps[]`, but only for
  steps with a record in the output store: what was deployed, not what will run. `graph` lists
  document references.
- **`--follow ndjson` streams steps only while they run.** `_line_follow_callbacks()`
  (`deploy_command.py`) emits `{"event": "progress", "message": ...}` with the step in free text.
  The `-o json` envelope of `deploy run|plan|apply` carries diagnostics, no step list.
- **Selection.** `run_deployment()` (`deploy_controller.py`) takes `workspace.spec.execution`,
  orders it with `ordered_by_depends_on()` (`build_controller.py`), reverses it for `destroy`,
  then keeps the steps whose name equals `--stage` and whose `scope` equals `--scope`. Both
  options take one value; they are declared twice in `deploy_command.py` (`_shared_deploy_options`
  for `setup|check|plan|apply|destroy`, inline on `run`).
- **No match.** `deploy setup|check|plan|apply|run` log "no steps match" and exit 0; `deploy
  destroy` errors with `destroy_no_steps`, because exit 0 would audit a destroy that touched
  nothing.
- **`scope` is a free-form label** on `ProvisioningStepModel`; several steps may share it.
- **"Stage" means two things.** `--stage NAME` names a workspace execution step;
  `spec.stages[]` on a deployment (`DeploymentStageModel`) holds runtime parameters for a step,
  matched by `stage.step`. Of those, only `timeouts` is applied; `enabled`, `on_failure`,
  `health_checks`, `scripts`, `secrets`, `namespace` and `helm_namespaces` are accepted and unused.
- **Integration facts per step.** `find_provisioner()` and `resolve_integration()`
  (`integration_resolution.py`) give the bound integration of a step. Its class says
  `SUPPORTS_SAVED_PLAN` (false for `script` and `gitops`: `deploy apply` refuses them),
  `supports_destroy` (an instance property; `script` depends on its provisioner) and
  `CAPABILITIES` (`CONTAINER` for Helm and Compose). `integration.config.spec.version` is the
  required version; `integration.transport == "cli"` means a local tool named
  `integration.command`.
- **Readiness.** `assess_integration(integration, read_version=...)` (`integration_readiness.py`)
  checks enabled, PATH and version, with no credentials or network. `deploy run`'s preflight calls
  it once per `(integration.name, integration.command)`.
- **Topology expansion.** `effective_resources_and_namespaces(step, resources_by_name,
  topologies_by_name)` (`workspace_model.py`) gives a step's direct plus topology-expanded
  resources and namespaces. Callers build `resources_by_name` from enabled resources only.
- **Cross-job outputs work.** A step reads an upstream step's `${output:...}` values from the
  workspace's `output_store` when the upstream step ran in an earlier invocation, so one job per
  step or per scope, in dependency order, is a supported shape.
- **`--pin`** replaces the deployment's `spec.version` for one run (`resolve_deployment()`,
  `value_controller.py`). A version document holds pins (images, charts, remotes, artifacts,
  assets) and promotion metadata, never steps.

### Design

#### Name and scope of the command (S1)

`strata deploy steps DEPLOYMENT`, registered on `deploy_command` next to `list`, `images`,
`output` and `report`. "Steps", not "stages": the rows are workspace execution steps, and
`spec.stages[]` already means per-step parameters.

Everything in the output is about a step or derived from steps: scopes are groups of steps;
tools, capabilities, targets and timeouts are per step. Information about the deployment as a
whole stays with the commands that own it: `deploy list` (workspace, tenant, version, ring,
environments), `deploy images`, `deploy output`, `deploy report`. If the command ever needs more
than steps, rename it before release (`deploy describe` is the candidate) rather than stretch
"steps".

#### Usage

```
strata deploy steps DEPLOYMENT [--stage NAME]... [--scope LABEL]... [--destroy]
                               [--check-tools] [--path DIR] [-o console|json] [-q] [-v]
```

| Option | Meaning |
| --- | --- |
| `--stage NAME` | Keep the step named NAME. Repeatable. |
| `--scope LABEL` | Keep the steps whose `scope:` is LABEL. Repeatable. |
| `--destroy` | List in teardown order, as `deploy destroy` runs them. |
| `--check-tools` | Check each listed step's tool on this machine. |
| `--path DIR` | Where to start looking for the solution (as `deploy list`). |
| `-o`, `-q`, `-v` | The shared output options. `-v` adds effective targets to the console table. |

No `--pin` (S11a), no `--build-path` (nothing is read from the build output), no `--follow`
(nothing runs).

#### Resolution (S2, S3, S4)

- The deployment is resolved through `extends` and its workspace found with
  `resolve_deployment_workspace()`, the function `build run`, `deploy run` and `policy` use. An
  unknown deployment, or one that names no workspace, is a usage error (exit 2) with that
  function's message. A partial deployment that names a workspace is listed like any other;
  `data.partial` says it is partial (`deploy list` already marks partials, and listing a base's
  steps is useful when writing it).
- The solution must be valid (`require_valid()`, exit 3), as for `deploy report` and
  `deploy images`: ordering assumes validated steps, and a pipeline that builds jobs from an
  invalid solution fails in the first job anyway.
- Without `--check-tools` the command reads the solution only: no tool, no PATH lookup, no output
  store, no credentials, no build output. It runs in the cheapest pipeline job.

#### Selection (S5, S5a)

One function, `select_steps()`, used by `deploy steps` and by `run_deployment()` for all six run
commands, so a selection a pipeline computes is the selection it runs.

- `--stage` and `--scope` are repeatable options (Click `multiple=True`), matching
  `deploy report --step`. No comma-separated values.
- A step is selected when, for each option that was given, the step matches one of its values:
  OR within an option, AND across the two. With neither option, every step is selected. This is
  the single-value behaviour of today, extended; one value of each behaves exactly as now.
- An empty selection is a result, not an error: `steps[]` is empty and the exit code is 0. A
  deployment may simply have no `infra` step; the pipeline checks the count
  (`jq '.data.steps | length'`).
- Every value that matched no step is listed in `data.unmatched` (`{"stages": [...], "scopes":
  [...]}`), without a warning, for a pipeline that wants to check it.
- The run commands keep their no-match behaviour: `setup|check|plan|apply|run` log "no steps
  match" and exit 0; `destroy` keeps `destroy_no_steps`.

#### Order and level (S6, S7)

- `order` is the step's position in `ordered_by_depends_on()` over **all** the workspace's steps,
  before selection. Numbers therefore stay the same whatever the filter, and two pipelines that
  list different scopes can compare them.
- `level` is 0 for a step with no `depends_on`, otherwise one more than the highest level among
  its dependencies, also computed over all steps. Steps on one level do not depend on each other
  and may run as parallel jobs. Strata itself still runs steps one at a time.
- `--destroy` reverses the direction: `order` counts in teardown order (the reverse list
  `deploy destroy` uses), and `level` is 0 for a step nothing depends on, otherwise one more than
  the highest level among the steps that depend on it. `data.direction` is `apply` or `destroy`.
- `depends_on` is the step's declared list, unfiltered, so a pipeline running `--scope apps` sees
  that `apps` needs a step outside its selection.

#### Scopes (S8)

`data.scopes[]` has one entry per scope label among the selected steps; steps without a label are
grouped under `"scope": null`. Each entry has:

- `steps`: the selected steps with that label, in `order`.
- `depends_on`: the other scopes this scope waits for, derived from all steps' `depends_on`
  edges: scope A depends on scope B when a step labelled A depends on a step labelled B (A != B).
  In destroy direction the edges are reversed. Scopes outside the selection still appear here.
- `orderable`: false when the scope is on a cycle of scope edges (an `infra` step depends on an
  `apps` step and the other way round). Each such scope also gets a warning diagnostic (exit
  stays 0) saying that one job per scope cannot be ordered and one job per step can.

Entries are sorted in scope order (topological over the scope edges, ties by the lowest `order`
of their steps); scopes on a cycle sort by the lowest `order` of their steps.

#### Per-step fields (S9, S10, S12, S13)

- **Tool.** `integration`: `name`, `type`, `transport`, `command` (the executable when
  `transport` is `cli`, else `null`), `required_version` (`spec.version` of the integration
  document, else `null`). Read from the solution. If the integration's transport configuration is
  invalid, `transport` is `null` and the problem is reported under `--check-tools` (it is a
  readiness problem, and `deploy run` reports it the same way).
- **Capabilities.** `saved_plan` (`SUPPORTS_SAVED_PLAN`, or always true for a `CONTAINER`
  integration, which takes the container path in `deploy plan|apply`), `destroy`
  (`supports_destroy` of the bound instance), `container` (`Capability.CONTAINER`). These are the
  conditions `deploy apply` and `deploy destroy` check before anything runs; a pipeline uses them
  to send a `script` step to `deploy run` instead of a plan/apply pair.
- **Targets.** `targets`: the step's own lists (`resources`, `namespaces`, `topologies`,
  `dns_zones`, `firewalls`, `networks`, `assets`). `effective`: `resources` and `namespaces` from
  `effective_resources_and_namespaces()`, built from enabled resources only, sorted. A disabled
  resource is not applied, so it is not in `effective`.
- **Stage parameters.** `description` and `timeouts` from the deployment's `spec.stages[]` entry
  for the step, `null` when there is none.
- **Only what would be applied.** Stage fields strata accepts but ignores are not shown. When one
  starts to work it is added: when `enabled` is applied, a disabled step drops out of `steps[]`
  because it would not run.

#### Tool readiness (S13)

`--check-tools` adds `readiness` to every row: `available`, `installed_version`, `tool_path`,
`problems[]` and `warnings[]` (each `{code, message}`), from `assess_integration(read_version=True)`,
called once per `(integration.name, integration.command)` as in `deploy run`'s preflight. Without
the flag `readiness` is `null`.

- A problem is an error diagnostic located at the step, with the readiness code
  (`integration_unavailable`, `integration_version_mismatch`, ...); a warning is a warning
  diagnostic. Any problem makes the exit code 3.
- With `--destroy`, a problem that only concerns flags a teardown does not pass
  (`flags_only`) is a warning, as in `deploy destroy`.
- Only the listed steps' tools are checked. Other integrations (value stores, scanners, the
  output store) and connection checks stay with `strata integration check|list`, which remains
  the readiness command. `--check-tools` never contacts a remote service.
- Exit 3 follows the other listing commands: listing exits 0; an opt-in live check that fails
  exits 3 (`values list` live mode; `integration list --deployment`, the closest match;
  `deploy images` under `image_tag_policy`). It does not contradict S5a: an empty selection is a
  result, a tool that cannot run is a failed check.

#### Exit codes

```
0  listed (also when the selection is empty, and with scope-cycle warnings)
2  bad arguments, an unknown DEPLOYMENT, a deployment with no workspace, or not inside a solution
3  the solution is invalid, or --check-tools found a tool that is not ready
1  system failure
```

#### JSON contract (`-o json`)

Inside the standard envelope `{ok, command, version, context, summary, diagnostics, data}`:

| Field | Type | Meaning |
| --- | --- | --- |
| `deployment` | string | The DEPLOYMENT argument. |
| `workspace` | string | Resolved through `extends`. |
| `partial` | bool | The deployment is a partial base. |
| `direction` | `apply` \| `destroy` | `destroy` with `--destroy`. |
| `filters` | `{stages: [], scopes: []}` | The values given. |
| `unmatched` | `{stages: [], scopes: []}` | Values that matched no step. |
| `tools_checked` | bool | `--check-tools` was given. |
| `steps[]` | object | Selected steps, by `order`. |
| `steps[].name` / `scope` | string / string \| null | |
| `steps[].order` / `level` | int | Over all steps, in `direction`. |
| `steps[].depends_on` | string[] | Declared, unfiltered. |
| `steps[].provisioner` | string | |
| `steps[].integration` | object | `name`, `type`, `transport`, `command`, `required_version`. |
| `steps[].readiness` | object \| null | See Tool readiness. |
| `steps[].capabilities` | object | `saved_plan`, `destroy`, `container`. |
| `steps[].targets` | object | Declared lists, empty list when absent. |
| `steps[].effective` | object | `resources`, `namespaces`. |
| `steps[].description` / `timeouts` | string \| null / object \| null | From `spec.stages[]`. |
| `scopes[]` | object | `scope`, `steps`, `depends_on`, `orderable`. |

Example, with `--check-tools`:

```json
{
  "deployment": "shop-prod-weu",
  "workspace": "shop",
  "partial": false,
  "direction": "apply",
  "filters": {"stages": [], "scopes": []},
  "unmatched": {"stages": [], "scopes": []},
  "tools_checked": true,
  "steps": [
    {
      "name": "network",
      "scope": "infra",
      "order": 0,
      "level": 0,
      "depends_on": [],
      "provisioner": "core-tf",
      "integration": {"name": "terraform", "type": "terraform", "transport": "cli",
                      "command": "terraform", "required_version": ">= 1.9"},
      "readiness": {"available": true, "installed_version": "1.9.8",
                    "tool_path": "/usr/local/bin/terraform", "problems": [], "warnings": []},
      "capabilities": {"saved_plan": true, "destroy": true, "container": false},
      "targets": {"resources": ["vnet"], "namespaces": [], "topologies": ["core"],
                  "dns_zones": [], "firewalls": [], "networks": ["hub"], "assets": []},
      "effective": {"resources": ["nsg-app", "subnet-app", "vnet"], "namespaces": []},
      "description": null,
      "timeouts": {"setup": null, "check": null, "plan": 900, "apply": 1800, "destroy": null}
    },
    {
      "name": "apps",
      "scope": "apps",
      "order": 1,
      "level": 1,
      "depends_on": ["network"],
      "provisioner": "shop-helm",
      "integration": {"name": "helm", "type": "helm", "transport": "cli",
                      "command": "helm", "required_version": null},
      "readiness": {"available": true, "installed_version": "4.0.1",
                    "tool_path": "/usr/local/bin/helm", "problems": [], "warnings": []},
      "capabilities": {"saved_plan": true, "destroy": true, "container": true},
      "targets": {"resources": [], "namespaces": ["shop"], "topologies": [],
                  "dns_zones": [], "firewalls": [], "networks": [], "assets": []},
      "effective": {"resources": [], "namespaces": ["shop"]},
      "description": null,
      "timeouts": null
    }
  ],
  "scopes": [
    {"scope": "infra", "steps": ["network"], "depends_on": [], "orderable": true},
    {"scope": "apps", "steps": ["apps"], "depends_on": ["infra"], "orderable": true}
  ]
}
```

#### Console output

```
shop-prod-weu  workspace shop, apply order

  ORDER  LEVEL  STEP     SCOPE  TOOL       DEPENDS ON  PLAN/APPLY  DESTROY  TOPOLOGIES
  0      0      network  infra  terraform  -           yes         yes      core
  1      1      apps     apps   helm       network     container   yes      -

  Scopes: infra -> apps
```

`-v` prints each step's effective resources and namespaces under its row. `--check-tools` adds a
`TOOL` readiness column (`ok 1.9.8`, or the first problem). An empty selection prints
`(no steps match)` and the unmatched values.

#### Pipeline use

One job per scope, in scope order:

```sh
strata deploy steps "$D" -o json | jq -c '[.data.scopes[] | {scope, depends_on}]'
```

Route each step to the right command:

```sh
strata deploy steps "$D" -o json | jq -r '.data.steps[]
  | "\(.name) \(if .capabilities.saved_plan then "plan+apply" else "run" end)"'
```

Skip a job when its scope has no steps in this deployment:

```sh
n=$(strata deploy steps "$D" --scope infra -o json | jq '.data.steps | length')
[ "$n" -eq 0 ] && echo "no infra steps" && exit 0
```

Check the deploy agent's tools first:

```sh
strata deploy steps "$D" --scope apps --check-tools -q
```

#### Deliberately not in it

- **No `--pin` (S11a).** Steps come from `spec.workspace`, which a pin does not touch. A remote's
  pinned git ref changes the code a step runs, not which steps run or their order.
- **No unused stage fields (S10).** See Per-step fields.
- **No deployment-level data (S1).** See Name and scope.
- **No remote contact, ever.** Not even with `--check-tools`.

## Implementation Plan

Four phases, each a reviewable commit with its tests green (`nox` sessions as in CI). Phase 1
changes existing commands; phases 2 to 4 add the new one.

### Phase 1 - Repeatable `--stage`/`--scope` on the run commands

1. New `src/strata/controllers/step_selection.py`:
   - `@dataclass(frozen=True) StepSelection`: `steps: list[ProvisioningStepModel]`,
     `unmatched_stages: list[str]`, `unmatched_scopes: list[str]`.
   - `select_steps(steps, *, stages: Sequence[str] = (), scopes: Sequence[str] = ()) ->
     StepSelection`: keeps input order; OR within, AND across; unmatched values computed against
     all steps (a value is unmatched when no step in `steps` has that name/label at all, so a
     stage that exists but is excluded by `--scope` is not "unmatched").
2. `deploy_controller.py` `run_deployment()`: `stage: str | None` / `scope: str | None` become
   `stages: Sequence[str] = ()` / `scopes: Sequence[str] = ()`; lines 1185-1188 become one
   `select_steps()` call. No-match handling unchanged (log for run commands,
   `destroy_no_steps` for destroy). Update the docstring.
3. `deploy_command.py`: `--stage`/`--scope` get `multiple=True` in `_shared_deploy_options` and
   on `deploy run`; help text says "Repeatable"; parameters become `tuple[str, ...]` and are
   passed through `_run_partial_deploy`, `deploy apply`, `deploy destroy` and `deploy run`.
4. Tests:
   - `tests/strata/controllers/test_step_selection.py` (new): no filter, one stage, two stages,
     one scope, two scopes, stage AND scope, unmatched stage, unmatched scope, stage excluded by
     scope is not unmatched, order preserved.
   - `test_deploy_controller.py` / `test_deploy_destroy.py`: existing `--stage`/`--scope` cases
     keep passing with tuples; add two-scope and two-stage runs; destroy with no match still
     errors.
   - `test_commands_deploy.py`: `--scope a --scope b` reaches the controller as a tuple.
5. Docs: `docs/design/deploy-command.md` and `docs/design/deploy-pipeline-stage-commands.md`
   (option descriptions), `docs/how-to/ci-deploy-pipeline.md` (repeatable filters).

Done when a single `--stage`/`--scope` behaves exactly as before and repeated values select the
union.

### Phase 2 - Controller: `deployment_steps()`

1. New `src/strata/controllers/deployment_steps.py` with frozen dataclasses `StepTool`,
   `StepReadiness`, `StepCapabilities`, `StepRow`, `ScopeRow`, `DeploymentSteps` (with
   `to_data()` producing the JSON contract above and `diagnostics: Diagnostics`).
2. `deployment_steps(context, deployment_name, *, stages=(), scopes=(), destroy=False,
   check_tools=False) -> DeploymentSteps`:
   - `resolve_deployment_workspace(context, deployment_name)` (UsageError -> exit 2).
   - `all_steps = workspace.spec.execution or []`; `ordered = ordered_by_depends_on(all_steps)`,
     reversed for destroy.
   - `_levels(all_steps, destroy)`: longest-path levels over `depends_on` (reversed edges for
     destroy), iterating `ordered` so every dependency is seen first.
   - `select_steps(ordered, stages=..., scopes=...)` from phase 1.
   - Per selected step: `find_provisioner()`, `resolve_integration()`; `StepTool` from the
     integration (catch `IntegrationError` on `transport` -> `None`); capabilities from the class
     constants and `supports_destroy`; targets from the step; effective from
     `effective_resources_and_namespaces()` with `resources_by_name` of enabled resources and
     `topologies_by_name`, as `workspace_model.py` builds them; stage parameters from
     `deployment.spec.stages` matched by `step`.
   - `_scopes(all_steps, selected, destroy)`: scope edges, cycle detection by reachability on the
     (small) scope graph, warning per cyclic scope, sort as specified.
   - Readiness left `None` here (phase 4).
3. Tests in `tests/strata/controllers/test_deployment_steps.py` (new), with workspaces built in
   the test as the existing deploy controller tests do:
   - order and level on a diamond (`a`; `b`, `c` depend on `a`; `d` on `b` and `c`): levels
     0, 1, 1, 2; destroy direction: `d` level 0, `a` level 2.
   - order and level unchanged by a filter.
   - workspace inherited through `extends`.
   - scope summary: `infra -> apps`, unlabelled steps under `null`, a scope cycle gives
     `orderable: false` and a warning.
   - capabilities for terraform, script (with and without destroy support), gitops, helm.
   - effective targets: a topology expands; a disabled resource is left out.
   - stage parameters: `timeouts` present, absent stage gives `null`; unused fields never
     appear in `to_data()`.
   - unknown deployment and a deployment without workspace raise `UsageError`.

### Phase 3 - Command: `strata deploy steps`

1. New `src/strata/commands/deploy_steps_command.py`, modelled on `deploy_list_command.py` and
   `deploy_report_command.py`: options as in Usage, `command_run("deploy steps", ...)`,
   `open_solution(...).require_valid()`, `run.describe(...)`, one `run.step()` summary
   ("N step(s) in M scope(s)"), console table (`_print_rows`, the `_table` style of
   `deploy report`), `run.reporter.data = result.to_data()` for JSON, `run.ok =
   result.diagnostics.ok`. Docstring carries the exit codes and the JSON fields, like
   `deploy list`.
2. Register in `deploy_command.py` with `deploy_command.add_command(deploy_steps_command)`.
3. Tests in `tests/strata/commands/test_commands_deploy_steps.py` (new): console table, `-o json`
   envelope and contract, repeated filters, empty selection exits 0 with `unmatched`, unknown
   deployment exits 2, invalid solution exits 3, `--destroy`, `-v` effective targets.

### Phase 4 - `--check-tools`

1. In `deployment_steps()`: when `check_tools`, call `assess_integration(integration,
   read_version=True)` once per `(integration.name, integration.command)`, fill `StepReadiness`,
   and add diagnostics: problems as errors (warnings under `--destroy` when `flags_only`),
   notices as warnings, located at the step with the readiness code.
2. Command: `--check-tools` flag, readiness column in the console table.
3. Tests (controller and command), with the tool lookup and version read patched as
   `test_integration_readiness`-style tests do: tool missing (exit 3, `integration_unavailable`),
   wrong version (exit 3, `integration_version_mismatch`), ready (exit 0), one tool shared by two
   steps is assessed once, `flags_only` problem under `--destroy` is a warning, no flag means no
   tool contact (`readiness: null`).

### Phase 5 - Docs and release

1. `docs/how-to/ci-deploy-pipeline.md`: a "Plan jobs from the step list" section with the
   pipeline examples above, next to the `deploy list` matrix example.
2. `docs/design/deploy-command.md`: `deploy steps` in the command reference.
3. `.github/CHANGELOG.md`: new command; `--stage`/`--scope` repeatable on all deploy commands.
   `VERSION.txt` to `2.7.0` (new command and a backwards-compatible option change).
4. Move this doc to `docs/design/deploy-steps-command.md`, collapse decisions and changelog into
   `## History`, fix links to it.

## Related Decisions

- [deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md) — the
  `setup`/`check`/`plan`/`apply` split; this command picks which steps each of those jobs runs.
- [deploy-command.md](../design/deploy-command.md) — `--stage`/`--scope` and why `scope` lives on
  the step.
- [deploy-destroy.md](../design/deploy-destroy.md) — teardown order, `destroy_no_steps`,
  `flags_only` problems.
- [vscode-integrations-policies-views.md](vscode-integrations-policies-views.md) V1/V8 —
  `deploy list` as the deployment matrix; this is the same idea one level down.

## Remaining Work / Open Questions

No open design questions. Build phases:

- [ ] Phase 1 - repeatable `--stage`/`--scope`, `select_steps()`
- [ ] Phase 2 - `deployment_steps()` controller
- [ ] Phase 3 - `strata deploy steps` command
- [ ] Phase 4 - `--check-tools`
- [ ] Phase 5 - docs, changelog, release 2.7.0, move to `docs/design/`

## Changelog

- 2026-11-12: Draft. Problem, verified current behaviour, decisions S1-S11, JSON and console
  shapes, open questions.
- 2026-11-12: Review. A filter that matches nothing returns an empty list with exit 0, and the run
  commands stay as they are (S5a, was Q1). `--stage`/`--scope` are repeatable on `deploy steps` and
  on the run commands, with one shared filter (S5, was Q4). S1 says when the name stops fitting.
- 2026-11-12: Review. Declared and effective targets, topology expanded (S12, was Q2). Tools per
  step always, readiness behind `--check-tools`; readiness itself stays `integration check`
  (S13, was Q3). Only what would be applied is shown, now and once the stage fields work (S10,
  was Q5).
- 2026-11-12: No `--pin`: a version document cannot change the steps (S11a, was Q6). `--check-tools`
  exit 3 follows `values list` live mode and `integration list --deployment` (S13).
- 2026-11-12: Full design and implementation plan. Added: order and level computed over all steps
  so they do not change with a filter; scope edges from all steps; `partial`, `filters`,
  `tools_checked` and `integration.transport` in the data; `saved_plan` true for container
  steps; exit codes; five build phases.
