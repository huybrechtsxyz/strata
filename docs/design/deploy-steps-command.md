# `strata deploy steps` — Design

- Status: current
- Last updated: 2026-11-12

## Overview

`strata deploy steps DEPLOYMENT` lists a deployment's execution steps exactly as `deploy run`
selects and orders them, so a CI pipeline can build its jobs, matrix or approval gates from
`-o json` instead of hardcoding step names or parsing workspace YAML. For each step it gives the
scope, the order and dependency level, the tool it needs, whether `deploy apply` and
`deploy destroy` can take it, its targets and its stage timeouts; for each scope, the scopes it
waits for. It runs nothing.

It is the step-level counterpart of `strata deploy list` (one row per deployment, the deployment
matrix). `--stage` and `--scope` select through `select_steps()`, the same function every deploy
command uses, and are repeatable on all of them, so what `deploy steps` lists is what the run
commands run.

## Current Design

### Usage

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
| `--path DIR` | Where to start looking for the solution. |
| `-o`, `-q`, `-v` | The shared output options. `-v` adds effective targets to the console table. |

No `--pin`, `--build-path` or `--follow`: a version document cannot change the steps, nothing is
read from the build output, and nothing runs.

### Scope of the command

Everything in the output is about a step or derived from steps. Information about the deployment
as a whole stays with the commands that own it: `deploy list` (workspace, tenant, version, ring,
environments), `deploy images`, `deploy output`, `deploy report`. If the command ever needs more
than steps, rename it (`deploy describe`) rather than stretch "steps".

### Resolution

- The deployment is resolved through `extends`, and its workspace found with
  `resolve_deployment_workspace()` (`value_controller.py`), as `build run` and `deploy run` do.
  An unknown deployment, or one that names no workspace, is a usage error (exit 2). A partial
  deployment that names a workspace is listed; `data.partial` says it is partial.
- The solution must be valid (`require_valid()`, exit 3): ordering assumes validated steps.
- Without `--check-tools` the command reads the solution only: no tool, no `PATH` lookup, no
  output store, no credentials, no build output.

### Selection

`select_steps(steps, stages=..., scopes=...)` (`step_selection.py`) is used by `deploy steps` and
by `deploy_run()` (`deploy_controller.py`) for `deploy setup|check|plan|apply|run|destroy`.

- A step is kept when, for each option given, it matches one of that option's values: OR within
  an option, AND across the two. With neither option every step is kept.
- An empty selection is a result: `steps[]` is empty and the exit code is 0. A pipeline checks
  the count. Every value that matched no step at all is in `data.unmatched`; a stage that exists
  but is excluded by `--scope` is not unmatched.
- The run commands treat an empty selection as before: `setup|check|plan|apply|run` log "no
  steps match" and exit 0; `destroy` fails with `destroy_no_steps`, because exit 0 would audit a
  destroy that touched nothing.

### Order and level

- `order` is the step's position in `ordered_by_depends_on()` over **all** the workspace's steps,
  before selection, so it does not change with a filter.
- `level` is 0 for a step with no `depends_on`, otherwise one more than the highest level among
  its dependencies, also over all steps. Steps on one level do not depend on each other and may
  run as parallel jobs; strata itself runs steps one at a time.
- `--destroy` reverses the direction: `order` counts in teardown order, and `level` is 0 for a
  step nothing depends on, otherwise one more than the highest level among its dependents.
  `data.direction` is `apply` or `destroy`.
- `depends_on` is the step's declared list, unfiltered.

### Scopes

`data.scopes[]` has one entry per scope label among the selected steps; steps without a label are
grouped under `"scope": null`.

- `steps`: the selected steps with that label, in `order`.
- `depends_on`: the scopes this scope waits for, from all steps' `depends_on` edges (scope A waits
  for B when a step labelled A depends on a step labelled B). Reversed for `--destroy`. Scopes
  outside the selection appear here too.
- `orderable`: false when the scope is on a cycle of scope edges. Each such scope also gets a
  `scope_cycle` warning (exit stays 0): one job per scope cannot be ordered, one job per step can.

Entries are in scope order: topological over the scope edges, ties by each scope's first step;
scopes on a cycle last, by their first step.

### Per-step fields

- **`integration`**: `name`, `type`, `transport`, `command` (the executable when `transport` is
  `cli`), `required_version` (the integration document's `spec.version`). Read from the solution.
  An invalid transport configuration gives `transport: null`; `--check-tools` reports it.
- **`capabilities`**: `saved_plan` (`SUPPORTS_SAVED_PLAN`, or true for a `CONTAINER` integration,
  which takes the container path in `deploy plan|apply`), `destroy` (`supports_destroy` of the
  bound instance), `container` (`Capability.CONTAINER`). These are the checks `deploy apply` and
  `deploy destroy` make before anything runs. `script` and GitOps steps have `saved_plan: false`.
- **`targets`**: the step's own lists (`resources`, `namespaces`, `topologies`, `dns_zones`,
  `firewalls`, `networks`, `assets`). **`effective`**: `resources` and `namespaces` from
  `effective_resources_and_namespaces()`, with topologies expanded and disabled resources left
  out, as the deploy itself computes them.
- **`description`**, **`timeouts`**: from the deployment's `spec.stages[]` entry for the step.
- **Only what would be applied is shown.** Stage fields strata accepts but ignores (`enabled`,
  `on_failure`, `health_checks`, `scripts`, `secrets`, `namespace`, `helm_namespaces`) are not in
  the output. When one starts to work it is added; when `enabled` is applied, a disabled step
  drops out of `steps[]`.

### Tool readiness (`--check-tools`)

`readiness` on each row: `available`, `installed_version`, `tool_path`, `problems[]`,
`warnings[]` (`{code, message}`), from `assess_integration(read_version=True)`, called once per
`(integration.name, integration.command)`. Without the flag `readiness` is `null`.

- Findings are reported once per tool, at the first step that uses it, worded as `deploy run`'s
  preflight words them: problems as errors (exit 3), notices as warnings. Under `--destroy` a
  `flags_only` problem is a warning, as in `deploy destroy`.
- Only the listed steps' tools are checked; never a remote service. Readiness of every
  integration, with connection checks, stays `strata integration check|list`.
- Exit 3 follows the other listing commands: a listing exits 0, an opt-in live check that fails
  exits 3 (`values list` live mode, `integration list --deployment`, `deploy images` under
  `image_tag_policy`).

### Exit codes

```
0  listed (also when nothing matched, and with scope-cycle warnings)
2  bad arguments, an unknown DEPLOYMENT or one without a workspace, or not inside a solution
3  the solution is invalid, or --check-tools found a tool that is not ready
1  system failure
```

### JSON (`-o json`)

Inside the standard envelope `{ok, command, version, context, summary, diagnostics, data}`:

| Field | Type | Meaning |
| --- | --- | --- |
| `deployment` | string | The DEPLOYMENT argument. |
| `workspace` | string | Resolved through `extends`. |
| `partial` | bool | The deployment is a partial base. |
| `direction` | `apply` \| `destroy` | `destroy` with `--destroy`. |
| `filters` | `{stages, scopes}` | The values given. |
| `unmatched` | `{stages, scopes}` | Values that matched no step. |
| `tools_checked` | bool | `--check-tools` was given. |
| `steps[]` | object | Selected steps, by `order`. |
| `steps[].name` / `scope` | string / string \| null | |
| `steps[].order` / `level` | int | Over all steps, in `direction`. |
| `steps[].depends_on` | string[] | Declared, unfiltered. |
| `steps[].provisioner` | string | |
| `steps[].integration` | object | `name`, `type`, `transport`, `command`, `required_version`. |
| `steps[].readiness` | object \| null | With `--check-tools`. |
| `steps[].capabilities` | object | `saved_plan`, `destroy`, `container`. |
| `steps[].targets` | object | Declared lists, empty when absent. |
| `steps[].effective` | object | `resources`, `namespaces`. |
| `steps[].description` / `timeouts` | string \| null / object \| null | From `spec.stages[]`. |
| `scopes[]` | object | `scope`, `steps`, `depends_on`, `orderable`. |

```json
{
  "deployment": "shop-prod-weu",
  "workspace": "shop",
  "partial": false,
  "direction": "apply",
  "filters": {"stages": [], "scopes": []},
  "unmatched": {"stages": [], "scopes": []},
  "tools_checked": false,
  "steps": [
    {
      "name": "network", "scope": "infra", "order": 0, "level": 0, "depends_on": [],
      "provisioner": "core-tf",
      "integration": {"name": "terraform", "type": "terraform", "transport": "cli",
                      "command": "terraform", "required_version": ">= 1.9"},
      "readiness": null,
      "capabilities": {"saved_plan": true, "destroy": true, "container": false},
      "targets": {"resources": ["vnet"], "namespaces": [], "topologies": ["core"],
                  "dns_zones": [], "firewalls": [], "networks": ["hub"], "assets": []},
      "effective": {"resources": ["nsg-app", "subnet-app", "vnet"], "namespaces": []},
      "description": null,
      "timeouts": {"setup": null, "check": null, "plan": 900, "apply": 1800, "destroy": null}
    },
    {
      "name": "apps", "scope": "apps", "order": 1, "level": 1, "depends_on": ["network"],
      "provisioner": "shop-helm",
      "integration": {"name": "helm", "type": "helm", "transport": "cli",
                      "command": "helm", "required_version": null},
      "readiness": null,
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

### Console

```
shop-prod-weu  workspace shop, apply order

  ORDER  LEVEL  STEP     SCOPE  TOOL       DEPENDS ON  PLAN/APPLY  DESTROY  TOPOLOGIES
  0      0      network  infra  terraform  -           yes         yes      core
  1      1      apps     apps   helm       network     container   yes      -

  Scopes: infra -> apps
```

`-v` prints each step's effective resources and namespaces under its row. `--check-tools` adds a
`TOOL READY` column (`ok <version>`, or the first problem's code). An empty selection prints
`(no steps match)` and the values that matched nothing. Pipeline examples are in
[ci-deploy-pipeline.md](../how-to/ci-deploy-pipeline.md#plan-jobs-from-the-step-list).

### Code

- `src/strata/controllers/step_selection.py` — `select_steps()`, `StepSelection`.
- `src/strata/controllers/deployment_steps.py` — `deployment_steps()`, the row dataclasses and
  `DeploymentSteps.to_data()`; order, level, scope summary and readiness.
- `src/strata/commands/deploy_steps_command.py` — the command and its console table, registered
  on `deploy_command`.
- `src/strata/commands/deploy_command.py` — `--stage`/`--scope` with `multiple=True`
  (`_STAGE_HELP`, `_SCOPE_HELP`), passed to `deploy_run(stages=..., scopes=...)`.
- Tests: `tests/strata/controllers/test_step_selection.py`,
  `tests/strata/controllers/test_deployment_steps.py`,
  `tests/strata/commands/test_commands_deploy_steps.py`, and the repeated-filter cases in
  `test_deploy_controller.py`, `test_deploy_destroy.py` and `test_commands_deploy.py`.

## Related Decisions

- [deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md) — the
  `setup`/`check`/`plan`/`apply` split; this command picks which steps each of those jobs runs.
- [deploy-command.md](deploy-command.md) — `--stage`/`--scope` and why `scope` lives on the step.
- [deploy-destroy.md](deploy-destroy.md) — teardown order, `destroy_no_steps`, `flags_only`
  problems.

## History

- Before this command a pipeline could run a slice of a deployment but not discover the slices:
  `deploy list` stops at the deployment, `deploy report` lists only steps already deployed, and
  `--follow ndjson` names steps only in free text while they run.
- "steps", not "stages": `--stage` names an execution step, while a deployment's `spec.stages[]`
  holds parameters for a step. The name holds because every field is per step or derived from
  steps.
- A filter that matches nothing was first proposed as exit 2, as `deploy list` does to stop a
  typo yielding an empty matrix. Rejected: a deployment without an `infra` step is normal, and a
  pipeline checks the count. `data.unmatched` keeps the typo visible without failing.
- Making `deploy run|plan|apply` fail on an empty selection was considered and dropped for the
  same reason; only `deploy destroy` keeps its error, for its audit record.
- Repeatable `--stage`/`--scope` went into every deploy command, not only `deploy steps`, so a
  selection a pipeline computes is always one it can run.
- Order and level are computed before filtering so numbers are comparable across jobs that list
  different scopes.
- Tool readiness was first left out (the planning job often runs on another agent than the deploy
  jobs). It came back as opt-in `--check-tools`, scoped to the listed steps' tools, with the tool
  names always listed; full readiness stays `integration check`.
- No `--pin`: a version document pins images, charts, remotes, artifacts and assets, never the
  workspace or its steps.
