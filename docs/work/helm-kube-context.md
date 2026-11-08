# Helm kube context - Work

- Status: in-progress (implemented; open questions remain)
- Last updated: 2026-11-07

## Overview

A Helm step deploys to whatever Kubernetes cluster the caller's active kube context points at. Strata never says
which cluster it expects, so with the wrong context active a production `deploy run` installs into a development
cluster, or a `deploy destroy` uninstalls releases from the wrong one, without a warning.

This doc designs an optional `kube_context` on a deployment. When set, every `helm` call of the deployment runs
against that context and no other, whatever context is active, and the deploy stops before the first `helm` call
if the context does not exist. The caller still provides the kubeconfig, as ADR-0020 decided; strata only names the
context to use from it.

It is gap 6 of [helm-gap-analysis.md](helm-gap-analysis.md).

## Current state

Verified against the source and Helm 3.13.2 on 2026-11-07.

- Strata sets neither `KUBECONFIG` nor `--kube-context`. [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md):
  "`strata deploy run` for Helm expects `KUBECONFIG` already set by the caller (haven tunnels it via SSH
  port-forward itself)". Because of the tunnel, the cluster's server URL in haven's kubeconfig is a local port, so
  the server URL says nothing about which cluster it is. The context name is the only name the caller controls.
- Every `helm` call goes through `Integration.run()` → `run_command()`, which runs with `{**os.environ, **env}`. The
  `env` comes from the deploy controller (`tf_var_env()`), so one extra variable there reaches every `helm` call of a
  step: `upgrade` (apply and `--dry-run`), `uninstall`, `get manifest` (the destroy preview), `repo add` and
  `registry login`.
- Helm reads `HELM_KUBECONTEXT` as the default for `--kube-context`. Probed: `HELM_KUBECONTEXT=nope helm list -A`
  fails with `Kubernetes cluster unreachable: context "nope" does not exist`, before contacting anything.
- `HELM_KUBEAPISERVER` and `HELM_KUBETOKEN`, when set, override the server and credentials of whichever context is
  used. A caller that sets them is not targeting a context at all.
- The check phase (`helm lint`) does not contact a cluster.
- A deployment already carries everything else that differs per environment (`environments`, `tenant`, `providers`),
  and `extends` lets a partial base hold the stages while each leaf deployment names its own cluster.

## Design

### The field

```yaml
kind: deployment
meta:
  name: forge-prd
spec:
  extends: forge-base
  environments: [prd]
  kube_context: forge-prd
```

`DeploymentSpecModel.kube_context: str | None`: the name of a context in the caller's kubeconfig. It merges through
`extends` like any scalar (the child wins). It is a literal: value tokens are not resolved, as in `release_name`.

It is set on the deployment, not on a stage or a workspace:

- A deployment is one environment, so one cluster. Written once, it covers every Helm step.
- A stage would repeat it per Helm step, and two stages could disagree.
- A workspace is deployed to several environments, so it has no single cluster.

A workspace with Helm steps for two different clusters would need a per-stage override. No configuration has one
today (haven's Helm steps all target forge), so it is left out until one does.

### Pinning, not comparing

When `kube_context` is set, the controller adds `HELM_KUBECONTEXT=<kube_context>` to the `env` of every Helm step.
Helm then uses that context from the caller's kubeconfig for every call, whichever context is active.

Comparing the active context with the expected one and stopping on a mismatch was the other option. Pinning is
stricter and simpler: there is no window between the check and the `helm` call, the caller does not have to switch
context first, and strata does not need to know how Helm picks the active context.

### The pre-flight check

Before the first `helm` call of a deployment that has `kube_context` and runs a Helm step, strata reads the
caller's kubeconfig and stops with an error, before anything is installed, uninstalled or previewed, when:

| Code                         | When                                                                                     |
| ---------------------------- | ---------------------------------------------------------------------------------------- |
| `helm_kube_context_missing`  | no kubeconfig file defines a context named `kube_context`                                |
| `helm_kube_context_conflict` | the caller sets `HELM_KUBECONTEXT` to another context, or sets `HELM_KUBEAPISERVER`      |

```
error  Deployment 'forge-prd': kube context 'forge-prd' is not in the kubeconfig (KUBECONFIG=/run/haven/kubeconfig,
       contexts: 'forge-dev', 'k3d-local'). Strata deploys Helm only to the context the deployment names.
       [helm_kube_context_missing]
```

- The kubeconfig is the files in `KUBECONFIG` (separated by `os.pathsep`), else `~/.kube/config`, as Helm reads
  it. Only the context names are read, nothing else; a file that does not exist is skipped, as Helm skips it.
- Without the pre-flight check, Helm would report the same thing, but once per module and after earlier steps of the
  deployment (Terraform, for example) have already run.
- The cluster is not contacted. Whether it is reachable stays Helm's error, as today.
- It runs for `deploy run`, `deploy plan`, `deploy apply` and `deploy destroy`, including `--dry-run`. `--smoke-test`
  contacts no tool and skips it. `build run` and `strata validate` do not need a kubeconfig and do not read it.

Each Helm step prints the context it uses: `step 'applications_forge' via helm: kube context 'forge-prd'`.

### When `kube_context` is not set

Nothing changes at deploy: Helm uses the active context, as today. So existing configurations keep working.

`strata validate` gives a warning, `helm_kube_context_unset`, on each deployable deployment (not on `partial: true`
bases; on the merged result of `extends`) that runs a Helm step and has no `kube_context`:

```
warning  Deployment 'forge-prd' runs Helm step 'applications_forge' with no spec.kube_context, so it deploys to
         whichever kube context is active. Set spec.kube_context to the context of the deployment's
         cluster.  [helm_kube_context_unset]
```

A warning does not stop build or deploy. Making it an error is a later, separate decision, once haven sets the field.

### Validation of the field

- An empty string is a schema error.
- A value containing `${` is an error: tokens are not resolved here, and a context literally named `${var:x}` is
  never what was meant. (Implemented as a schema error with the Compose work, not a `helm_kube_context_token` code.)
- No other format rule: kubeconfig context names are free strings (`arn:aws:eks:...`, `user@cluster`).

### What does not change

- Strata never sets or passes `KUBECONFIG`, never writes to a kubeconfig, and never switches the caller's active
  context. ADR-0020 stands.
- Compose and Terraform steps get no `HELM_KUBECONTEXT` (it is added for the `helm` integration only). Compose has
  the same gap with `DOCKER_HOST` and Docker contexts, designed separately in
  [compose-docker-context.md](compose-docker-context.md).
- Argo CD (a git commit, no cluster call) is unaffected.

## Implementation plan

- [x] 1. `kube_context: str | None` on `DeploymentSpecModel` (non-empty), merged by `merge_deployment_specs()`,
  with a field description (the generated schema picks it up)
- [x] 2. `kubeconfig_context_names(environ) -> (paths, names)` in `integrations/helm.py`: reads the context names
  from the `KUBECONFIG` files or `~/.kube/config`, skipping missing files
- [x] 3. Deploy controller: before the first step of a deployment with `kube_context` and a Helm step, the
  pre-flight check (`helm_kube_context_missing`, `helm_kube_context_conflict`); for each Helm step, add
  `HELM_KUBECONTEXT` to `env` and print the context line
- [x] 4. Semantic checks: `helm_kube_context_unset` (warning); the token check later moved to the schema
- [x] 5. Tests: the context names from one file, from two files in `KUBECONFIG`, a missing file, no kubeconfig at
  all; the pre-flight check stops before any `helm` call (missing context, caller's `HELM_KUBECONTEXT` differs,
  `HELM_KUBEAPISERVER` set) and passes when the caller's `HELM_KUBECONTEXT` is the same; every Helm call of the step
  gets `HELM_KUBECONTEXT`, a Terraform step does not; destroy and dry-run are pinned too; `--smoke-test` skips the
  check; `extends` merges the field; the warning on a deployment with a Helm step and none on one without, or on a
  partial base; the token error; one real `helm` run with `HELM_KUBECONTEXT` set to a missing context, to keep the
  probe above honest
- [x] 6. Docs: [deployment.md](../config/deployment.md), [helm-chart-deployment.md](../design/helm-chart-deployment.md)
  (targeting and Diagnostics), [deploy-command.md](../design/deploy-command.md), the changelog, and gap 6 in
  [helm-gap-analysis.md](helm-gap-analysis.md)

## Related Decisions

- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) - the caller provides `KUBECONFIG`; unchanged
- [helm-gap-analysis.md](helm-gap-analysis.md) - gap 6
- [helm-release-names.md](helm-release-names.md) - the release and namespace checks (gap 7)

## Remaining Work / Open Questions

- **haven's context name.** Which context does haven's tunnelled kubeconfig define, and is the name the same on
  every machine and in CI? If the pipeline and a laptop name the same cluster differently, one of them renames its
  context (`kubectl config rename-context`). A list of accepted names would avoid that, at the cost of a weaker pin.
- **Warning or error when unset.** This design warns. Once haven sets `kube_context`, an error for every deployment
  with a Helm step would make the pin mandatory.
- **The audit manifest.** Recording the context in the deployment manifest (`ManifestStageModel`) would show later
  which cluster a run targeted. Not needed for the guard itself.

## Changelog

- 2026-11-07: Created.
- 2026-11-07: Implemented. The JSON schemas are generated from the models into a solution's `.strata/schemas/` by `strata sln`, so no checked-in file needed regenerating.
