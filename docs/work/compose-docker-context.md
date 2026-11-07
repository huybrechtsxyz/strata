# Compose docker context - Work

- Status: in-progress (implemented; open questions remain)
- Last updated: 2026-11-07

## Overview

A Compose step deploys a Swarm stack to whatever Docker engine the caller's environment points at: `DOCKER_HOST`, else
`DOCKER_CONTEXT`, else the context chosen with `docker context use`. Strata never says which engine it expects, so
with the wrong one active a production `deploy run` deploys the stack to a development swarm, and a `deploy destroy`
removes a stack from the wrong one.

This is the Compose counterpart of [helm-kube-context.md](helm-kube-context.md), which pins Helm to a deployment's
`kube_context`. The design is the same: an optional `docker_context` on a deployment pins every `docker` call of
its Compose steps to that Docker context, and the deploy stops before the first step if the context does not exist or
the environment points Docker elsewhere.

## Current state

Verified against the source and Docker CLI 28.3.2 on 2026-11-07. The probes ran with `DOCKER_CONFIG` pointed at a
scratch directory, so the user's own Docker configuration was not touched.

### What strata runs

`ComposeIntegration` (`integrations/compose.py`) runs, per namespace:

| Phase           | Command                                              | Contacts the engine |
| --------------- | ---------------------------------------------------- | ------------------- |
| deploy          | `docker stack deploy --with-registry-auth -c F NS`   | yes                 |
| dry-run         | `docker stack config -c F`                           | no (renders only)   |
| destroy         | `docker stack rm --detach=false NS`                  | yes                 |
| destroy preview | `docker stack services NS`                           | yes                 |
| readiness       | `docker --version`                                   | no                  |

Every call goes through `Integration.run()` with the step's `env`, merged over `os.environ`, as for Helm. Compose has
no check phase. Strata sets no Docker variable.

### How Docker picks the engine

Probed:

| Environment / flag                                         | Engine used                                      |
| ---------------------------------------------------------- | ------------------------------------------------ |
| `DOCKER_CONTEXT=nope`                                      | fails before connecting: `context "nope": context not found` |
| `DOCKER_CONTEXT=probe`                                     | the `probe` context's endpoint                   |
| `DOCKER_HOST=tcp://127.0.0.1:1` and `DOCKER_CONTEXT=probe` | `DOCKER_HOST`: **the context is silently ignored** |
| `DOCKER_HOST=...` and `--context probe`                    | the `probe` context (the flag wins)              |
| `DOCKER_CONTEXT=nope` with `docker stack config`           | succeeds: `stack config` never resolves the context |

- `docker context inspect NAME` reads only the local context store (`$DOCKER_CONFIG/contexts`, else `~/.docker`). It
  exits 1 for an unknown context and does not contact any engine.
- `default` is a built-in context that always exists: the local engine, or `DOCKER_HOST` when that is set.
- [deploy-destroy.md](../design/deploy-destroy.md) D7 already names the failure this prevents: "a destroy pointed at the
  wrong `DOCKER_HOST`".

## Design

### The field

```yaml
kind: deployment
meta:
  name: hearth-prd
spec:
  extends: hearth-base
  environments: [prd]
  docker_context: hearth-prd
```

`DeploymentSpecModel.docker_context: str | None`: the name of a Docker context the caller has created
(`docker context create hearth-prd --docker host=ssh://deploy@hearth`). It is set per deployment, merges through
`extends`, and is a literal (no value tokens), for the same reasons as `kube_context`. The two fields are independent:
a deployment with both Helm and Compose steps sets both.

### Pinning

When `docker_context` is set, the controller adds `DOCKER_CONTEXT=<docker_context>` to the `env` of every Compose step,
so `stack deploy`, `stack config`, `stack rm` and `stack services` all use that context.

`DOCKER_HOST` overrides `DOCKER_CONTEXT` without a word, so a pin through the environment holds only if `DOCKER_HOST`
is unset. The pre-flight check below makes that an error rather than passing `--context` on every command line, which
would win over `DOCKER_HOST`:

- the caller who set `DOCKER_HOST` meant an engine, and quietly deploying somewhere else is the confusion this guards
  against;
- it keeps the argv shapes unchanged and the mechanism the same as Helm's (`HELM_KUBECONTEXT`).

### The pre-flight check

Before the first step of a deployment that has `docker_context` and runs a Compose step, strata stops with an error,
before any tool runs, when:

| Code                               | When                                                                     |
| ---------------------------------- | ------------------------------------------------------------------------ |
| `compose_docker_context_missing`   | `docker context inspect <docker_context>` fails: Docker has no such context |
| `compose_docker_context_conflict`  | the caller sets `DOCKER_HOST`, or sets `DOCKER_CONTEXT` to another context |

```
error  Deployment 'hearth-prd' deploys Compose to docker context 'hearth-prd', but DOCKER_HOST is set in the
       environment, and it overrides the context. Unset it.  [compose_docker_context_conflict]
```

- The check asks Docker itself (`docker context inspect`), unlike the Helm check, which reads the kubeconfig. The Docker
  context store is a directory of hashed folders, and Docker's own answer covers `DOCKER_CONFIG` for free. The call is
  local and fast.
- It runs for `deploy run`, `plan`, `apply` and `destroy`, including `--dry-run`. That matters most for the dry-run:
  `docker stack config` never resolves the context, so without this check a dry-run against a missing context would
  pass and only the real deploy would fail.
- `--smoke-test` contacts no tool and only says it would check the context.
- `docker_context: default` is allowed and means the local engine. Because `DOCKER_HOST` is rejected, it cannot mean
  anything else.

Each Compose step prints the context it uses: `step 'apply_hearth' via ComposeIntegration: docker context 'hearth-prd'`.

### When `docker_context` is not set

Nothing changes at deploy. `strata validate` warns, `compose_docker_context_unset`, on a deployable deployment that
runs a Compose step (a provisioner whose tool, or named integration, is `compose`) and has no `docker_context`. A
`${...}` token in either context field is a schema error (`DeploymentSpecModel`), which replaced the planned
`*_token` codes: a literal-only rule needs no cross-document check.

### One mechanism for both tools

The Helm pin is written for Helm only (`integration.TYPE == HelmIntegration.TYPE` in `deploy_run()`). With a second
tool, the tool-specific parts move onto the integration, and the controller stops naming tools:

- `InfraIntegration.TARGET_CONTEXT_FIELD: str | None`: the deployment field that pins it (`"kube_context"`,
  `"docker_context"`); `None` for every other integration.
- `InfraIntegration.target_context_env(name) -> dict[str, str]`: the variable that pins it (`HELM_KUBECONTEXT`,
  `DOCKER_CONTEXT`).
- `InfraIntegration.target_context_problem(name, environ) -> tuple[str, str] | None`: the pre-flight check. Helm's is
  today's `kube_context_problem()`; Compose's runs `docker context inspect`.

`deploy_run()` then checks each distinct (integration, context) pair among the steps it will run, once, before the
first step, and adds the variable per step. The validate warning uses the same field name, so it covers both tools with
one check, keeping the existing `helm_kube_context_*` codes and adding the `compose_docker_context_*` ones.

### What does not change

- Strata never sets `DOCKER_HOST`, creates or changes a Docker context, or runs `docker context use`.
- Terraform steps get no `DOCKER_CONTEXT`. A Terraform Docker provider has its own `host` setting, which is that
  module's business.
- Image builds and pushes: strata does not run them.

## Implementation plan

- [x] 1. `docker_context: str | None` on `DeploymentSpecModel` (non-empty), with a field description
- [x] 2. `TARGET_CONTEXT_FIELD`, `target_context_env()` and `target_context_problem()` on `InfraIntegration`
  (defaults: no field, `{}`, `None`); Helm moves its pin onto them; Compose implements them with `DOCKER_CONTEXT`,
  the `DOCKER_HOST` / `DOCKER_CONTEXT` conflicts and `docker context inspect`
- [x] 3. `deploy_run()`: replace the Helm-only block with the generic one (check once per pair, before the first step;
  add the variable per step; print the context line)
- [x] 4. Semantic checks: generalise `_check_deployment_kube_context()` to every integration type with a
  `TARGET_CONTEXT_FIELD` (`*_unset` warning, `*_token` error)
- [x] 5. Tests: every Compose call (deploy, dry-run, `stack rm`, `stack services`) gets `DOCKER_CONTEXT`; a missing
  context, `DOCKER_HOST` set, another `DOCKER_CONTEXT` each stop the run before any tool runs (including a dry-run);
  the same `DOCKER_CONTEXT` is accepted; `--smoke-test` and a run without a Compose step skip the check; a deployment
  with Helm and Compose steps pins each to its own context; the validate warning and token error for Compose; the
  existing Helm tests still pass unchanged; one real `docker` run with `DOCKER_CONFIG` in a temporary directory that
  confirms `docker context inspect` fails for an unknown context and `DOCKER_HOST` overrides `DOCKER_CONTEXT` (so a
  Docker change in that order shows up as a failing test)
- [x] 6. Docs: [deployment.md](../config/deployment.md) (the cluster section becomes "Choosing the target", covering
  both), [deploy-destroy.md](../design/deploy-destroy.md) D7, [deploy-command.md](../design/deploy-command.md), the
  changelog, and the gap note in [helm-gap-analysis.md](helm-gap-analysis.md)

## Related Decisions

- [helm-kube-context.md](helm-kube-context.md) - the Helm pin this mirrors
- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) - the caller provides the connection; unchanged
- [deploy-destroy.md](../design/deploy-destroy.md) - D7, a destroy pointed at the wrong `DOCKER_HOST`

## Remaining Work / Open Questions

- **How haven reaches its Docker engine today.** If haven's workflow sets `DOCKER_HOST=ssh://...`, setting
  `docker_context` makes that an error until the workflow creates a context instead
  (`docker context create hearth --docker host=ssh://...`). That is the intended migration, but it is a change to
  haven's workflow, not only its config.
- **Warning or error when unset.** As for `kube_context`: a warning now, an error once haven sets the field.

## Changelog

- 2026-11-07: Created.
- 2026-11-07: Implemented. Deviation: the `${...}` token check is a schema error on both fields, not a semantic
  `*_token` code, so `helm_kube_context_token` is gone (it was never released). The validate warning's code is
  `<integration type>_<field>_unset`.
