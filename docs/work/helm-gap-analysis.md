# Helm gap analysis - Work

- Status: in-progress
- Last updated: 2026-11-07

## Overview

A review of how strata builds, checks and deploys `type: helm` modules, looking for what is wrong or missing. It
reads the code (`integrations/helm.py`, the Helm path in `controllers/deploy_controller.py`, `models/module_model.py`)
against [helm-chart-deployment.md](../design/helm-chart-deployment.md), ADR-0042 and ADR-0043, and tests the
suspect behaviour with real `helm` 3.13.2 on a small chart (`helm template`, no cluster).

Out of scope here, because it has its own doc: which image a Helm module runs
([helm-image-delivery.md](helm-image-delivery.md)).

## Current Design / Progress

What works and is covered by the design doc: local and registry charts (OCI and HTTP), `chart_version` and
`pins.charts` passed as `--version`, private registries with the password on stdin and an isolated Helm
configuration, `helm lint` of the built local chart as the check phase, `--set-string` for secrets, the
configuration key check, and `deploy destroy` with a manifest summary as its preview.

### Findings

Ranked by impact. "Verified" means reproduced with `helm` 3.13.2; "by reading" means it follows from the code but was
not run end to end (no cluster).

| #  | Gap                                                                    | Kind        | Impact | Evidence    |
| -- | ---------------------------------------------------------------------- | ----------- | ------ | ----------- |
| 1  | A local chart loses its own default values (fixed)                     | correctness | high   | verified    |
| 2  | Secret values are passed on the `helm` command line (fixed)            | security    | high   | by reading  |
| 3  | `deploy plan` / `--dry-run` streams the rendered manifest (fixed)      | security    | medium | by reading  |
| 4  | A registry chart with no values cannot deploy (fixed)                  | correctness | medium | verified    |
| 5  | Stage timeouts do not reach a Helm upgrade (fixed)                     | operability | medium | by reading  |
| 6  | The cluster is whatever kube context is active                         | safety      | medium | by design   |
| 7  | Release and Kubernetes namespace names are not validated (fixed)       | validation  | low    | by reading  |
| 8  | Registry charts get no check before deploy                             | validation  | low    | by reading  |
| 9  | Local chart dependencies are never built                               | correctness | low    | by reading  |
| 10 | Helm 4 compatibility is unknown                                        | risk        | low    | to verify   |

#### 1. A local chart loses its own default values

`prepare_namespace()` writes the module's values over the copied chart's `values.yaml`, and deploy passes that same
file with `-f`. Helm reads chart defaults from `values.yaml`, so every default the module does not repeat is gone. A
registry chart behaves differently: its defaults stay, and the module's values are an overlay. So the same
`configuration` means "overlay" for a registry chart and "replace everything" for a local one. A local module with
no `services` and no `configuration` keeps the chart's file, which is a third behaviour.

Reproduced: a chart with `image.repository` in its defaults and a module value of `replicas: 3` renders correctly as
an overlay (`-f over.yaml`) and fails with `nil pointer evaluating interface {}.repository` when the overlay replaces
`values.yaml`. That is the error [ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md) recorded
from haven and attributed to a configuration typo. This is likely the actual cause, and should be checked against
that haven run.

haven's 19 local charts pass today, probably because v1 had the same behaviour and their modules carry every value
the charts need. Fixing this adds defaults back, so a chart whose default conflicts with an intent the module
expressed by omission would change. Helm merges maps and replaces lists, so the risk is small.

**Done (2026-11-07), together with gap 4.** `prepare_namespace()` writes `strata-values.yaml` beside `meta.yaml` and
leaves the chart's `values.yaml` alone; deploy passes `-f strata-values.yaml`. A module directory without it (a build
from before this change) is an error, not a deploy without values. A real-Helm test renders a chart whose defaults
the module does not repeat. Still open: the haven comparison in the open questions, since a module that relied on
dropped defaults now gets them.

#### 2. Secret values are passed on the `helm` command line

`deploy_namespace()` delivers each secret-shaped leaf as `--set-string path=value`. The argument list is visible to
other users of the machine (`ps`, Task Manager, process auditing on CI agents). ADR-0042 rejected `--password` for
exactly this reason, and states that `run_command()` forbids that leak class. Registry passwords follow that rule;
module secrets do not.

A dot inside a key was not a problem: the paths escape it as `\.`, which `--set-string` also reads as a literal dot.
A secret inside a list was: the path `extraEnv.1.value` made `--set-string` create a map key `1` instead of
setting a list item. No consumer had one.

**Done (2026-11-07).** The secret leaves are a second values document on stdin (`-f strata-values.yaml -f -`), through the
existing `Integration.run(input=)`. Only the branches that lead to a secret are sent, and a list holding one is sent
whole, because Helm replaces lists. `_set_string_args()` and `_escape_set_value()` are removed. Verified with
`helm template` 3.13.2: the stdin value replaces the token left in the file, special characters survive, and `0123`
stays a string.

#### 3. `deploy plan` / `--dry-run` streams the rendered manifest with Secrets

The dry-run calls `helm upgrade --dry-run`, which prints the full rendered manifest, and streams it through
`line_callback` with `_redact()`. Redaction masks resolved secret values in plain text. It cannot mask a Kubernetes
`Secret`'s `data`, which is base64. [deploy-destroy.md](../design/deploy-destroy.md) (D7) avoids this for the destroy
preview by showing only `Kind/name`. The deploy preview does not.

**Done (2026-11-07).** The dry-run is not streamed. `summarise_dry_run()` reads its `HOOKS:` and `MANIFEST:` sections
and the step reports one `Kind/name` line per resource (hooks marked) through `on_step`, which `deploy_namespace()` now
takes, so the summary is shown without `--follow` too. `NOTES:` are dropped, because they are rendered from the same
values. A failed preview still reports Helm's stderr, which holds an error, not the manifest. A real diff (the
`helm diff` plugin) is a separate feature; see the open questions.

#### 4. A registry chart with no services or configuration cannot deploy

`prepare_namespace()` writes `values.yaml` only when there is something to write. A registry chart has no copied
`values.yaml`, so the file does not exist, but `plan()` and `deploy()` always pass `-f <module>/values.yaml`. Helm
fails with `open .../values.yaml: ... cannot find the path` (verified). `helm lint` skips registry charts, so nothing
catches it before deploy.

**Done (2026-11-07).** `strata-values.yaml` is always written, `{}` when the module sets nothing (see gap 1).

#### 5. Stage timeouts do not reach a Helm upgrade

`_timeout_kwargs()` is passed to the Helm check and destroy, not to `deploy_namespace()`. The upgrade always runs
with `--wait --atomic --timeout 5m` and a 600 s process timeout. A chart that needs longer (a large image pull, a
database migration hook) is rolled back by `--atomic`, and neither `stage.timeouts.apply` nor
`STRATA_TIMEOUT_<STEP>_APPLY` can change that. [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md)
deferred this. [cli-timeout.md](../design/cli-timeout.md) already calls the two timeouts a footgun.

**Done (2026-11-07).** `deploy_run()` passes the `plan` (dry-run) or `apply` timeout to `deploy_namespace()`, for
Compose too, which had the same gap. For Helm it is per release: N seconds is the process timeout and `--timeout` gets
N/2 (`_upgrade_timeouts()`), because `--atomic` waits again while it rolls back. That keeps the defaults as the N = 600
case (600 s and `5m`), so nothing changes without an override. A dry-run gets the process timeout only.

#### 6. The cluster is whatever kube context is active

Nothing in strata sets `KUBECONFIG` or `--kube-context`.
[ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) decided the caller provides `KUBECONFIG` (haven
tunnels it itself), so this is by design. The gap is that nothing checks it: with the wrong context active, a
production deployment installs into a development cluster without warning.

Options, smallest first: print the active context at the start of a Helm step; an optional expected context
per deployment stage, checked before any `helm` call; or strata passing `--kube-context` and `--kubeconfig`
itself. The second keeps the ADR-0020 split and only adds a guard.

**Done (2026-11-07)**, as designed in [helm-kube-context.md](helm-kube-context.md): an optional `kube_context` on the
deployment, which pins every `helm` call to that context through `HELM_KUBECONTEXT`, with a check before the first
step that the kubeconfig defines it. `strata validate` warns when a deployment with a Helm step has none. Compose has the
same gap with `DOCKER_HOST`, closed the same way by `docker_context`
([compose-docker-context.md](compose-docker-context.md)).

#### 7. Release and Kubernetes namespace names are not validated

`release_name` (default: the module reference name) and `kubernetes_namespace` (default: the strata namespace name)
are free strings. Helm requires a DNS-1123 release name of at most 53 characters, and Kubernetes requires a DNS-1123
label for the namespace. A name like `My_App` passes `strata validate` and fails at deploy. Two module attachments
that end up with the same release name in the same Kubernetes namespace (possible through `kubernetes_namespace`)
would install one over the other, and nothing detects it.

**Done (2026-11-07)**, as designed in [helm-release-names.md](helm-release-names.md). Helm modules only for now (Argo CD later), and strict:
both rules are errors in `strata validate`, defaults included, and the (Kubernetes namespace, release) pair must be
unique within a workspace.

#### 8. Registry charts get no check before deploy

The configuration key check and `helm lint` both need a chart on disk, so they run only for local charts. haven has
6 registry charts. A misspelt `configuration` key on one of them is silently ignored by Helm.

Fix, at the check phase (which may use the network, unlike `build run`): `helm show values` for the key check, and
`helm template` with the overlay for a lint equivalent. Both need the registry login from ADR-0042, so they belong
inside the same isolated configuration.

#### 9. Local chart dependencies are never built

A local chart that declares `dependencies:` in `Chart.yaml` and does not vendor them in `charts/` fails lint and
install with "found in Chart.yaml, but missing in charts/ directory". Strata never runs `helm dependency build`.
[sbom-generation.md](sbom-generation.md) notes the same need for rendering. Whether any haven chart has
unvendored dependencies is not checked; if none does, documenting "vendor your dependencies" may be enough.

#### 10. Helm 4 compatibility is unknown

The argv shapes were copied from v1 and tested with Helm 3. Helm 4 changed some upgrade flags (it renamed
`--atomic`, for example). Nothing checks the Helm version. This needs a run against Helm 4 to know whether anything
breaks.

### Smaller notes

- An anonymous HTTP repository is added to the user's own Helm configuration (`repo add --force-update`). Only
  credentialed modules are isolated. That is harmless, but the design doc's "the user's own Helm configuration is
  never touched" is true only for credentialed modules.
- `HelmIntegration.get_values()` is unused.
- Helm steps produce no `${output:}` values. That is consistent with v1 and has no known consumer.

## Related Decisions

- [ADR-0042](../decisions/0042-private-chart-registries-authenticate-through-the-remote-integration.md) - secrets never
  in argv (gap 2 breaks this for module secrets)
- [ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md) - the lint check phase; its haven evidence
  matches gap 1
- [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md) - deferred Helm timeouts (gap 5)
- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) - the caller provides `KUBECONFIG` (gap 6)
- [helm-chart-deployment.md](../design/helm-chart-deployment.md) - how Helm works today
- [helm-image-delivery.md](helm-image-delivery.md) - the image gap, tracked separately

## Remaining Work / Open Questions

### Proposed order

- [x] 1. Gap 1: write strata's values to their own file
- [ ] 1a. Check the ADR-0043 haven failure and haven's local charts against gap 1
- [x] 2. Gap 2: secrets on stdin with `-f -`
- [x] 3. Gap 4: always write the values file
- [x] 4. Gap 3: summarise the dry-run instead of streaming the manifest
- [x] 5. Gap 5: stage timeouts for plan and apply
- [x] 6. Gap 7: name checks and the release uniqueness check in `strata validate`
- [x] 7. Gap 6: the kube context pin
- [ ] 8. Gaps 8, 9 and 10: on demand, after checking haven

### Open questions

- **Gap 1 compatibility.** Does any haven module depend on the chart defaults being dropped? A `build run` of haven
  followed by `helm template` for each local chart, with and without the fix, answers it.
- **Gap 6 shape.** Decided: per deployment (one deployment is one environment, so one cluster). A per-stage override
  waits for a workspace whose Helm steps target two clusters.
- **A real plan diff.** Should `deploy plan` show what would change in the cluster (`helm diff upgrade`)? It needs a
  plugin, so it would be optional, with the summary from gap 3 as the fallback.
- **Release status and rollback.** No `deploy status` or rollback exists for Helm. No consumer has asked for either.

## Changelog

- 2026-11-07: gap 7 fixed (Helm only; Argo CD later).
- 2026-11-07: gap 5 fixed (Helm and Compose plan/apply timeouts).
- 2026-11-07: gaps 1 and 4 fixed (`strata-values.yaml`).
- 2026-11-07: gaps 2 and 3 fixed on `feature/helm-improvements`. Corrected gap 2: a dot in a key was handled; a list
  index was not.

- 2026-11-07: created. Gaps 1 and 4 and the `-f -` fix for gap 2 verified with `helm` 3.13.2 and a test chart; the
  rest from reading the source.
