# Helm 4 by default, Helm 3 on request - Work

- Status: implemented (open questions remain)
- Last updated: 2026-11-08
- Decision: [ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md)

## Overview

Gap 10 of [helm-gap-analysis.md](helm-gap-analysis.md). Strata works with Helm 4.3.1 today, but it passes flags that
Helm 4 deprecates (`--atomic`, a bare `--dry-run`) and a bare `--wait` whose meaning changed. Helm 3 is still what most
OMP machines run.

The design ([ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md)):

- Strata uses Helm 4's flags by default.
- A solution that has to stay on Helm 3 says so: a `helm` integration document whose `spec.version` excludes Helm 4
  (`"<4"`, `"~=3.16"`, `"==3.13.2"`). Only then does strata pass Helm 3's flags, which are today's command.
- Before anything applies, strata reads `helm version`. Helm 3 without that declaration stops the run with a message
  that says how to fix it, instead of failing at `helm upgrade` after earlier steps have applied.
- The Helm version that ran is shown in the deploy output and recorded in the deployment manifest.
- Helm 3 support is removed in a later release. Strata states no date: when is decided then, from Helm 3's support
  status at that time.

This is a breaking change for Helm 3 users who declare nothing: they must install Helm 4 or add the declaration.

## Current state

Verified on 2026-11-08 with Helm 4.3.1 and 3.13.2 (see the gap 10 section of the gap analysis for the full run).

| strata passes    | Helm 4.3.1                                                                                         | Helm 3                                                         |
| ---------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `--atomic`       | works, warns "use --rollback-on-failure"                                                           | the only form; `--rollback-on-failure` unknown                 |
| bare `--dry-run` | works, warns "replace with --dry-run=client"                                                       | a plain on/off flag before 3.13; `--dry-run=client` from 3.13  |
| bare `--wait`    | means `watcher` (kstatus; needs the `watch` RBAC verb)                                             | an on/off flag; `--wait=<mode>` rejected (checked with `legacy`) |
| (nothing)        | `--server-side auto`: server-side apply for a new release, the previous method for an existing one | client-side three-way merge only                               |

From `helm upgrade --help` (4.3.1): "The --wait flag will be defaulted to "watcher" if --rollback-on-failure is set".

Strata does not know which Helm it runs. It reads `helm version` only when a `helm` integration document declares
`spec.version`, compares it, and drops it: the deploy output and the deployment manifest do not show it.
`ManifestProviderModel` (`audit_manifest_model.py`) has room for a provisioner's tool, but nothing fills
`artifacts.providers` today.

Where Helm runs at OMP (GitHub search of `org:OMP`, 2026-11-08): shared Azure Linux build agents 3.5.1
(`lib-ansible`), a `vct_dp` pipeline 3.16.4 (pinned for the `helm-diff` plugin), Coder workspaces 3.22.0, one
devcontainer 4.2.0, and strata's own devcontainer `latest`. Most solutions declare no `helm` integration document.

Only `helm upgrade` changes. `lint`, `template`, `pull`, `repo add`, `registry login`, `dependency list`, `uninstall`
and `version` behave the same on both majors.

## Design

### The command

| Purpose                     | Helm 4 (the default)                | Helm 3 (declared; today's command) |
| --------------------------- | ----------------------------------- | ---------------------------------- |
| Preview (`plan()`)          | `--dry-run=client`                  | `--dry-run`                        |
| Deploy, wait                | `--wait=watcher`                    | `--wait`                           |
| Deploy, roll back a failure | `--rollback-on-failure`             | `--atomic`                         |
| Server-side apply           | not passed (Helm's default, `auto`) | not applicable                     |

The Helm 3 command is today's command byte for byte, so it works on every Helm 3 version, including the 3.5.1 build
agents. `--dry-run=client` would have needed 3.13.

`--wait=watcher` is Helm 4's own wait mode, and what a bare `--wait` already means on Helm 4, so a Helm 4 user waits
as today. It is passed explicitly so the command says what it does. It watches resources with kstatus, so the deploy
identity needs the `watch` verb on the resources the chart creates, where Helm 3's wait needed only `get` and `list`.

`--dry-run=client` is what Helm 4 does today for a bare `--dry-run`, so the preview does not change; only the warning
goes. `--dry-run=server` (validation by the API server) is a separate change.

### Choosing the command: the configuration, not the machine

The flags come from the `spec.version` of the Helm integration the step runs with, so one configuration gives the
same command on every machine. That integration is the step's provisioner binding (`resolve_integration()`): the
document its `integration:` names, else the one enabled `type: helm` document, else the type default, which has no
`spec.version`. Two provisioners bound to two `helm` documents can therefore choose different majors.

- Helm 3 is chosen when `spec.version` is declared and matches neither `4.0.0` nor `4.999.999`. So `"<4"`,
  `"~=3.16"`, `"==3.13.2"` and `">=3.8,<4"` choose Helm 3; `">=3"`, `">=4.1"` and no `spec.version` choose Helm 4.
  The two probes are a heuristic: a constraint built to dodge both (`"<4.5,!=4.0.0"`) is misread, and Helm 4 on
  `PATH` then fails readiness, not the deploy.
- `HelmIntegration.helm_major_declared() -> int` returns 3 or 4 from that rule. `plan()` and `deploy()` use it; they
  never run `helm version` themselves.
- Readiness already checks a declared `spec.version` against the installed Helm
  (`integration_version_mismatch`), so `"<4"` with Helm 4 on `PATH` stops before anything applies, as it does today.

A solution without a `helm` integration document gets Helm 4's flags. To stay on Helm 3 it declares one, which the
Helm provisioners then auto-bind:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: helm
spec:
  type: helm
  capabilities: [container]
  version: "<4"   # Helm 3 flags; deprecated
```

`build run` binds Helm by type only (`resolve_module_integration()`), but it uses no version-dependent flag, so the
binding difference does not matter there.

### Reading the installed version

Integration readiness ([integration-readiness.md](integration-readiness.md)) runs once per tool per command, before
anything is applied: in the deploy preflight (`deploy run`, `plan`, `apply`, `destroy`) for every step's tool, in the
`build run` preflight for each enabled integration document, and in `strata integration list`. It reads the version
only when `spec.version` is declared. Two generic additions:

- `Integration.READ_VERSION: ClassVar[bool] = False`. When true, readiness reads the version even without
  `spec.version`. Helm sets it. `--smoke-test` still reads nothing (`read_version=False`).
- `Integration.version_problems(installed: str) -> list[ReadinessProblem]` and
  `Integration.version_notices(installed: str) -> list[ReadinessProblem]`, both empty by default. Problems are
  reported as today (an error when the integration is used or required). Notices are always warnings and go to a new
  `IntegrationReadiness.warnings`.

Helm's rules, with `declared = helm_major_declared()` and `major` from the installed version:

| Installed    | Declared | Result                                                                                                                                                                  |
| ------------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.x or later | 4        | nothing (a future Helm 5 gets Helm 4's flags; revisit when it exists)                                                                                                  |
| 3.x          | 3        | notice `helm_3_deprecated`: "Helm {v} is deprecated in strata and will stop being supported in a later release. Install Helm 4 and remove the Helm 3 `spec.version`." |
| 3.x          | 4, nothing declared | problem `helm_3_not_declared`: "Helm {v} found, but strata uses Helm 4 flags unless a `helm` integration declares Helm 3. Install Helm 4, or declare `spec.version: \"<4\"` on a `helm` integration (deprecated)." |
| 3.x          | 4, a constraint that allows 4 (`">=3"`) | problem `helm_3_not_declared`: "... `spec.version` '{c}' allows Helm 4, so strata uses Helm 4 flags. Install Helm 4, or narrow it to `\"<4\"` (deprecated)." |
| 4.x          | 3        | `integration_version_mismatch` from the existing `spec.version` check                                                                                                   |
| 2.x or lower | any      | not reachable: Helm 2's `helm version` fails without Tiller, so it reads as unreadable                                                                                  |
| unreadable   | any      | nothing new; `integration_version_mismatch` when `spec.version` is declared, as today                                                                                   |

The messages name no date. When Helm 3 support ends is decided by the release that removes it.

An unreadable version is not a problem when nothing is declared. That leaves one way to fail mid-deploy: a Helm 3
whose `helm version` fails, with nothing declared, gets Helm 4's flags and `helm upgrade` stops with "unknown flag".
`helm version` failing while `helm upgrade` works is not a case seen so far, so it is accepted.

`helm version` reads no kubeconfig and contacts no cluster, so the extra call needs no credentials. The deploy
preflight keys readiness by tool (`readiness_by_tool`), so it asks once per Helm binding, not per step. The
integration instances are not shared (`resolve_integration()` builds a new one per provisioner), so `get_version()`
is not cached; `plan()` and `deploy()` do not need the installed version.

### Showing and recording the version

- **Deploy output:** the preflight prints one step line per tool whose version it read, for example
  `helm 4.3.1 (C:\tools\helm.exe)`. The path is `shutil.which(command)`, so a second Helm earlier on `PATH` is
  visible.
- **Deployment manifest:** `ManifestProviderModel` gets `tool_version: str | None` and `tool_path: str | None`, and
  `artifacts.providers` is filled with one entry per provisioner the run used (name, type, version, path). This
  fills a field the manifest already declares; `backend` and `details` stay empty in this gap. The manifest is built
  in `finalize_and_distribute_deploy_audit()` (`audit_run.py`), so the deploy controller passes it the preflight's
  readiness per provisioner.

This applies to every local tool readiness reads a version for, not only Helm. Terraform and Compose get a version
only when their document declares `spec.version`; otherwise their entry has `tool_version: null`, so the record is
uneven across tools until they set `READ_VERSION` too (a follow-up, not this gap).

### Diagnostics

| Code                       | Severity                      | When                                    |
| -------------------------- | ----------------------------- | --------------------------------------- |
| `helm_3_not_declared`      | error in every deploy command (`setup` and `check` included, so a staged pipeline fails at its first stage); warning in `deploy destroy`; elsewhere error when used or required, else warning | Helm 3.x installed, Helm 3 not declared |
| `helm_3_deprecated`        | warning                       | Helm 3.x installed and declared         |

The deploy preflight reports every readiness problem as an error and skips the step (`readiness.ok`); a notice must
not count against `ok`.

`deploy destroy` runs only `helm uninstall`, which has no version-dependent flag, so the flag choice cannot break it.
There `helm_3_not_declared` is reported as a warning and the step still runs: a teardown is never blocked by it. For
that, the problem is marked as concerning only the flags (`ReadinessProblem.flags_only = True`), and the destroy
preflight demotes such problems to warnings and keeps the step.

### Removing Helm 3 (a later release, no date set)

- Delete the Helm 3 command in `plan()` and `deploy()` and `helm_3_deprecated`.
- Add `helm_version_unsupported` (a problem): Helm 3 installed, or a `spec.version` that excludes Helm 4, replacing
  `helm_3_not_declared`.
- Record the breaking change in the CHANGELOG.

## Rollout and risk

- **Helm 3 users who declare nothing (most today, including the 3.5.1 agents):** the deploy stops in the preflight
  with `helm_3_not_declared` until they install Helm 4 or declare `"<4"`. A breaking change; the CHANGELOG and the
  release notes must say so, with the snippet above.
- **Helm 3 users who declare `"<4"`:** today's command, plus one warning per run.
- **Helm 4 users:** the deprecation warnings disappear; wait (already `watcher`), rollback and preview behave as
  today. They see one more `helm version` call per run.
- **Helm 3 users moving to Helm 4** get `watcher`: a deploy identity without the `watch` verb on the chart's resources
  fails the wait. Say so in the CHANGELOG and in helm-chart-deployment.md.
- **`build run`** does not use version-dependent flags, but its preflight assesses declared integration documents, so
  a declared `helm` document gets the same findings there.
- **Server-side apply, unchanged by this gap:** on Helm 4, a release first installed by Helm 4 uses server-side
  apply, and later upgrades keep it. A Helm 3 upgrade of that release applies its client-side merge over objects with
  server-side field managers. Not tested. With the declaration, a solution runs one Helm major everywhere, which
  avoids this unless agents disagree with the declaration (and then readiness stops them).
- **Tests** that assert the `helm upgrade` argv set the declared version; readiness tests stub `get_version()`.

## Related Decisions

- [ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md): this design's decision
- [ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md): lint is unaffected
- [integration-readiness.md](integration-readiness.md): where the version is read and reported
- [audit-trail.md](audit-trail.md): the deployment manifest
- [helm-chart-deployment.md](../design/helm-chart-deployment.md): how Helm works today

## Remaining Work / Open Questions

### Implementation plan

Five phases, each leaving the suite green. Phases 1 and 2 change nothing for any user; phase 3 is the breaking one.
After each phase: `uv run --no-sync pytest -q -o addopts="" -p no:cacheprovider` on the touched test files, ruff
format and check, mypy, lint-imports. Python files keep CRLF (edit with the Edit tool).

**Phase 1 - the readiness hooks (generic, no behaviour change)** - done 2026-11-08. As planned; `strata integration
check` prints the path in its description (`'terraform' on PATH, at /usr/bin/terraform, version 1.9.2`), and the
JSON readiness carries `tool_path` and `warnings` (each with `flags_only`). 4643 tests pass.

- `integrations/base.py`, on `Integration`:
  - `READ_VERSION: ClassVar[bool] = False`.
  - `version_problems(self, installed: str) -> list[ReadinessProblem]` and
    `version_notices(self, installed: str) -> list[ReadinessProblem]`, both returning `[]`.
  - `ReadinessProblem` lives in `controllers/integration_readiness.py`, and the layer contract (ADR-0003:
    controllers above integrations) forbids `integrations/` importing it. Move it to `integrations/base.py` and
    re-export it from `integration_readiness.py`, so existing imports keep working. Add `flags_only: bool = False`.
- `controllers/integration_readiness.py`:
  - `IntegrationReadiness` gains `warnings: tuple[ReadinessProblem, ...] = ()` and `tool_path: str | None = None`.
    `ok` stays `not self.problems`.
  - `assess_integration()`: read the version when `available and read_version and (required is not None or
    integration.READ_VERSION)`. Keep today's `spec.version` comparison. Then, with a version read, extend `problems`
    with `version_problems(installed)` and set `warnings` from `version_notices(installed)`. `tool_path` is
    `shutil.which(command)` for an available local tool. `_unconstructed()` passes the defaults.
  - `readiness_diagnostics()`: report `warnings` as warnings, after the same early return as problems (a disabled
    integration nothing uses reports nothing).
- `commands/integration_command.py`: `strata integration check` reports `warnings` as warnings; `_readiness_json()`
  adds `warnings` (code and message) and `path`; `_describe()` shows the path after the version.
- Tests (`tests/strata/controllers/test_integration_readiness.py`, with the existing fake integration):
  - `READ_VERSION` without `spec.version` reads the version once; with `read_version=False` it never runs the tool.
  - problems from `version_problems()` make it not `ok`; notices from `version_notices()` do not, and appear as
    warnings in `readiness_diagnostics()`, whatever `used` and `required` are.
  - `tool_path` is set for an available tool, `None` when missing.
  - `strata integration check` / `list --output json` carry `warnings` and `path` (`test_commands_integration*.py`).

**Phase 2 - the deploy preflight shows and keeps the result** - done 2026-11-08. Deviations: three call sites
finalize the audit, not two (`deploy run`, `deploy apply` and `deploy destroy`), and all three record the providers
through `_provider_recorder()` in `deploy_command.py` (first readiness per provisioner name). The preflight and
destroy tests live in `test_deploy_controller.py` beside the existing version-check tests (`_versioned_terraform`),
not in `test_deploy_destroy.py`. A destroy whose only problem is `flags_only` was run against the Terraform stub: the
step runs. 4651 tests pass.

- `controllers/deploy_controller.py`, the preflight loop (`readiness_by_tool`, around line 1150):
  - report `readiness.warnings` as warnings, once per tool key, like the problems;
  - under `destroy=True`, a problem with `flags_only` is reported as a warning and does not drop the step; every
    other problem stays an error. So `resolved_steps` takes the step when `readiness.ok` or (`destroy` and all its
    problems are `flags_only`);
  - one step line per tool key whose version was read: `"{type} {installed_version} ({tool_path})"`, not under
    `--smoke-test`;
  - new keyword `on_tool: Callable[[str, IntegrationReadiness], None] | None = None` on `deploy_run()`, called with
    the provisioner name and its readiness for every resolved step (not only the first of a tool key).
- `models/audit_manifest_model.py`, `ManifestProviderModel`: `tool_version: str | None = None`,
  `tool_path: str | None = None` (descriptions in the existing style). Optional fields, so old manifests still load.
- `controllers/audit_run.py`, `finalize_and_distribute_deploy_audit()`: new keyword
  `providers: Sequence[ManifestProviderModel] = ()`, written to `ManifestArtifactsModel.providers` (`None` when
  empty, as today).
- `commands/deploy_command.py`: the `deploy run` and `deploy destroy` call sites (the two that finalize the audit)
  collect `on_tool` into `ManifestProviderModel(name=provisioner, type=readiness.type,
  tool_version=readiness.installed_version, tool_path=readiness.tool_path)` and pass them as `providers=`.
- Tests:
  - `test_deploy_controller.py`: the step line appears once for two steps of one tool; warnings once; `on_tool` is
    called per step; `--smoke-test` makes no version call and prints no tool line.
  - `test_deploy_destroy.py`: a `flags_only` problem is a warning under destroy and the step runs; a problem without
    it still stops the step.
  - `test_audit_run.py` and `tests/strata/models/test_models_audit_manifest.py`: `providers` reaches the manifest;
    a manifest without the new fields still validates.

**Phase 3 - Helm 4 flags by default, Helm 3 when declared (the breaking change)** - done 2026-11-08. Deviations:
the tests that assert the exact calls a step makes turn the version probe off with one autouse fixture in
`test_deploy_controller.py` (`_no_helm_version_probe`) instead of each listing `helm version`; five end-to-end tests
(`_helm_versioned`) turn it back on and cover undeclared Helm 3 (stops before any step), declared Helm 3 (v1 flags
and the warning), Helm 4, `"<4"` with Helm 4 installed, and a destroy with undeclared Helm 3. `deploy setup`'s
command test now expects the probe as its only Helm call. Checked with the real binaries on 2026-11-08: Helm 4.3.1
accepts `--dry-run=client` and `--wait=watcher --rollback-on-failure` without a deprecation warning, Helm 3.13.2
accepts the v1 flags, and Helm 3.13.2 rejects `--wait=watcher` ("invalid argument"). The Helm test files pass with
Helm 4.3.1 first on `PATH`. 4677 tests pass.

- `integrations/helm.py`:
  - `READ_VERSION = True`.
  - `helm_major_declared(self) -> int`: `spec.version` from `self.config`; 3 when it is set and
    `parse_version_constraint(v)` contains neither `4.0.0` nor `4.999.999` (`prereleases=True`, as
    `version_satisfies()`), else 4.
  - `plan()`: `--dry-run=client` for 4, `--dry-run` for 3.
  - `deploy()`: for 4, `--wait=watcher` when `wait` and `--rollback-on-failure` when `atomic`; for 3, `--wait` and
    `--atomic` as today. Keep the parameter names `wait` and `atomic` (callers and `_upgrade_timeouts()` use them);
    rename only in the docstrings. Update the module docstring's command line and `_upgrade_timeouts()`'s
    `--atomic` sentence ("the rollback (`--rollback-on-failure`, or `--atomic` on Helm 3)").
  - `version_problems(installed)`: the major is `int(installed.split(".")[0])` when it parses, else no finding.
    Major 3 with `helm_major_declared() == 4` returns `helm_3_not_declared` with `flags_only=True`; the message
    variant depends on whether `spec.version` is set (nothing declared, or a constraint that allows Helm 4).
  - `version_notices(installed)`: major 3 with `helm_major_declared() == 3` returns `helm_3_deprecated`.
  - Codes as module constants beside `CHART_NOT_PINNED`.
- Tests (`tests/strata/integrations/test_integrations_helm.py`):
  - `helm_major_declared()` for `None`, `"<4"`, `"~=3.16"`, `"==3.13.2"`, `">=3.8,<4"` (3) and `">=3"`, `">=4.1"`
    (4).
  - the `plan()` and `deploy()` argv for 3 and 4, including `wait=False` and `atomic=False`.
  - `version_problems()`/`version_notices()` for every row of the rules table, including an unparseable version.
  - Every existing test that asserts a `helm upgrade` argv: decide per test whether it pins Helm 3 (a config with
    `version: "<4"`) or expects the Helm 4 argv. Expect most `test_deploy_controller.py` Helm argv assertions to
    change to the Helm 4 form.
  - Tests that build a `HelmIntegration` with a fake `run()` and assess readiness now see a `helm version` call;
    adjust their recorded-call expectations.
- Real Helm: run the Helm test files with Helm 4.3.1 first on `PATH` (no deprecation warning in the output any
  more), and with 3.13.2 and a `"<4"` declaration (the warning appears once).

**Phase 4 - documentation** - done 2026-11-08. Also updated beyond the list: `docs/design/cli-timeout.md` (the
`--dry-run=client` row and the rollback flag). The CHANGELOG marks the breaking change with a **Breaking:** prefix
under Changed, since it has no separate section. The gap analysis marks gaps 9 and 10 fixed in its table (9 had been
left unmarked). History mentions of `--atomic` (gap analysis, ADRs) are left as written.

- `docs/design/helm-chart-deployment.md`: a "Helm versions" section: the flag table, the `spec.version` rule and
  snippet, the readiness rules, `watcher` needing the `watch` verb, server-side apply left at Helm's default, and the
  diagnostics rows `helm_3_not_declared` and `helm_3_deprecated`; update every place that quotes `--atomic` or the
  upgrade command line.
- `docs/integrations/chart-registries.md`: the prerequisite "`helm` 3.x" becomes "Helm 4, or Helm 3 declared with
  `spec.version: "<4"` (deprecated)".
- `docs/work/integration-readiness.md`: `READ_VERSION`, version problems and notices, `warnings`, `flags_only`, and
  `tool_path`.
- `docs/work/audit-trail.md`: `artifacts.providers` is filled, with `tool_version` and `tool_path`.
- `docs/config/integration.md`: `spec.version` on a `helm` integration also chooses the Helm 3 flags.
- `.github/CHANGELOG.md`, Unreleased: **Breaking** (Helm 3 must be declared, with the snippet); **Changed** (Helm 4
  flags, `watcher`); **Added** (tool version and path in the deploy output and the manifest; readiness warnings in
  `strata integration` output).
- This document: status implemented, deviations; the gap analysis: gap 10 done in the order list and its changelog.

**Phase 5 - the whole suite** - done 2026-11-08. 4677 tests pass; ruff format and check, mypy and lint-imports
are clean; `strata validate config` finds no problems in the repo's 34 documents. `strata integration check helm`
with the real binaries: Helm 3.13.2 undeclared is `helm_3_not_declared`; Helm 4.3.1 undeclared is ready with no
finding; with `version: "<4"` (a scratch copy of `config/`), Helm 3.13.2 is `helm_3_deprecated` and Helm 4.3.1
is `integration_version_mismatch`.

- Full `pytest`, ruff, mypy, lint-imports; `strata validate config` on the repo's own solution.

**Not in this gap:** `READ_VERSION` for Terraform and Compose; `--dry-run=server`; the Helm 3 removal.

### Decided

- 2026-11-08: Helm 4 by default; Helm 3 only when a `helm` integration's `spec.version` excludes Helm 4; Helm 3
  without the declaration stops in the preflight. Chosen over detecting the version at run time, so the configuration,
  not the machine, decides the command ([ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md)).
- 2026-11-08: the Helm version that ran is shown in the deploy output and recorded in the deployment manifest.
- 2026-11-08: Helm 4 waits with `--wait=watcher`.
- 2026-11-08: no removal date in code, messages or docs; Helm 3 is "deprecated" until a release removes it.
- 2026-11-08: under `deploy destroy`, `helm_3_not_declared` is a warning and the step runs.

### Open questions

- Which OMP pipelines run strata with Helm 3 and need the declaration before this ships?
- Mixed Helm 3 and Helm 4 upgrades of one release under server-side apply (see Rollout and risk).

## Changelog

- 2026-11-08: review fixes. `deploy destroy` now also treats a `flags_only` problem of an integration
  document outside the selected steps as a warning (`integration_preflight(flags_only_as_warning=...)`), so a
  `required: true` Helm document no longer blocks a Terraform-only destroy. The version line and the manifest's
  `tool_version` use `IntegrationReadiness.reported_version`, which is `None` for tool output that is not a version.
  Kept: undeclared Helm 3 also stops `deploy setup` and `deploy check` (fail at the first stage); this document's
  diagnostics table said otherwise and is corrected. The CHANGELOG now says the probe runs only when a Helm step or
  a declared `helm` document is involved.
- 2026-11-08: proposed with the version detected at run time; changed the same day to Helm 4 by default and Helm 3
  only when declared in `spec.version`, with a preflight error for undeclared Helm 3 and the version recorded in the
  deployment manifest; Helm 4 waits with `watcher`; no removal date; ADR-0055.
