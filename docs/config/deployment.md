# Deployment (`kind: deployment`)

The "container instance" (ADR-0011): executes a [`workspace`](workspace.md)'s provisioning recipe
against a specific [`environment`](environment.md), for a specific [`tenant`](tenant.md). The
workspace says *what* runs and in what order; the deployment says *where, with which values, and
with what runtime behavior*. `strata build run`/`strata deploy run` both take a deployment name.

## Schema

- `meta.params` — the [parameter sets](parameterset.md) this deployment reads, per role:
  `{spoke: spoke-acme-001}`, or a list per role merged in order. Any document of the `extends`
  chain may name sets; per role they merge root first, leaf last. `${param:<role>.<key>}` in the
  spec of any document of the chain reads them — see "Parameter sets" below
- `meta.name_rule` — optional: the name every deployable deployment built on this one must have,
  with `${param:}` tokens filled from its values (`spoke-${param:spoke.name}`); any other name is
  `name_rule_mismatch`
- `spec.workspace` — the [`workspace`](workspace.md) document this deployment instantiates
- `spec.environments[]` — [`environment`](environment.md) documents, merged left-to-right (later wins)
- `spec.tenant` — optional [`tenant`](tenant.md) reference; its defaults merge in first
- `spec.version` — optional: the [`version`](version.md) document, or list of documents, whose pins this
  deployment runs with. The listed documents' pins apply together and each target may be pinned by only one of
  them (`version_pin_conflict`), so a shared document and a product document each own their own pins — see
  [Several documents per deployment](version.md#several-documents-per-deployment). `--pin NAME` (repeatable)
  replaces the list for one run
- `spec.kube_context` — the context in the caller's kubeconfig that this deployment's Helm steps deploy to — see
  "Choosing the target" below
- `spec.docker_context` — the Docker context that this deployment's Compose steps deploy to — see "Choosing the
  target" below
- `spec.extends` — another deployment document (typically `partial: true`) whose spec is merged
  in before this one's (child wins; nested blocks deep-merge; stages merge by step name; `verify.checks` merge by name, on the spec and in a stage; `providers` and `assets` merge by slot; `environments`, `configurations` and `version`
  lists append base-first, each entry once; other lists are replaced)
- `spec.partial` — `true` marks a base deployment as not independently deployable (a leaf picks it
  up via `extends`)
- `spec.providers[]` — rebind [`workspace`](workspace.md) provider slots for this deployment, each
  `{slot, provider}`: the workspace's `spec.providers[].slot` and the [`provider`](provider.md)
  document to use instead of the slot's default — see "Rebinding a provider" below
- `spec.assets[]` — rebind [`workspace`](workspace.md) asset slots for this deployment, each
  `{slot, asset}`: the workspace's `spec.assets[].slot` and the [`asset`](asset.md) document to use
  instead of the slot's default — see "Rebinding an asset" below
- `spec.documentation` — render the handover document after each successful `deploy run` and store it with the
  audit record — see "Handover document" below
- `spec.verify` — checks for the whole deployment, run once after every step was applied — see "Verifying a
  deploy" below
- `spec.locking` — deployment lock configuration
- `spec.layers` — deployment layer/scope segments
- `spec.stages[]` — runtime parameters per provisioning step, each:
  - `step` — the [`workspace`](workspace.md) `spec.execution[].name` these parameters apply to
    (never redefines what runs — only how)
  - `enabled` — bool, or a string expression (accepted but not yet evaluated)
  - `on_failure` — `stop`, `rollback`, or `continue`
  - `timeouts` — per-phase (`setup`/`check`/`plan`/`apply`/`destroy`/`verify`) overrides
  - `verify` — checks run after the step is applied (or destroyed), which decide whether the deploy worked — see
    "Verifying a deploy" below
  - `health_checks[]` — removed in 2.9 ([ADR-0068](../decisions/0068-remove-health-checks-in-2-9.md)): it never
    ran. A stage that declares it fails to load, and the message writes the `verify.checks` entries (`type: http`
    or `type: tcp`) to use instead
  - `secrets[]` — secret keys this stage needs injected
  - `namespace` / `helm_namespaces[]` — restrict a stage to specific namespaces
- `spec.configurations` / `spec.properties` / `spec.lifecycle` — passthrough/merge layers

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: deploy-base
  annotations:
    description: "Reusable base — extended per instance, not deployable alone"
spec:
  partial: true
  workspace: main
  # locking: ...
```

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: prd-deployment
  annotations:
    description: "Production instance — inherits structure from deploy-base"
  tags: ["production"]
spec:
  extends: deploy-base
  environments:
    - prd
  tenant: contoso
```

## Rebinding a provider

A [`workspace`](workspace.md) declares provider *slots* with a default document. A deployment
rebinds a slot to deploy the same workspace to another region or account — IaC code still
addresses `strata.providers.<slot>`; only the bound document (and its region, tags,
configuration) changes. Put the binding in a reusable `partial` base so every deployment in that
region just extends it:

```yaml
kind: deployment
meta:
  name: region-north
spec:
  partial: true
  extends: deploy-base
  providers:
    - slot: azure
      provider: azure-north     # another provider document of type azure
---
kind: deployment
meta:
  name: prd-north-deployment
spec:
  extends: region-north
  environments: [prd]
  tenant: contoso
```

- **Rebind only** — the bound document is used as-is; there is no field patching. Per-deployment
  values belong in [`environment`](environment.md) documents.
- Bindings merge by `slot` across `extends` (child wins), so a leaf can rebind another slot
  without dropping the base's.
- A tenant's `geographies` are checked against the **bound** provider's region, so a deployment
  can serve a tenant restricted to another geography only once it rebinds to a provider there.
- `strata validate` reports `provider_slot_unknown` (slot not declared by the workspace) and
  `provider_type_mismatch` (bound provider's `spec.type` differs from the slot's default) as
  errors, and `provider_binding_redundant` (binds the slot's own default) as a warning. A
  `partial` base is only judged once merged into the deployment that extends it, and the
  redundant warning is skipped for a document that itself uses `extends` (resetting a base's
  binding back to the default is legitimate).

## Rebinding an asset

The same rule applies to a workspace's [asset slots](workspace.md): a deployment binds a slot to
another [`asset`](asset.md) document. One workspace can then serve many customers, each with
files in their own repository, while the Terraform code keeps reading `local.strata_assets.<slot>`:

```yaml
kind: deployment
meta:
  name: prd-contoso-deployment
spec:
  extends: deploy-base
  environments: [prd]
  tenant: contoso
  assets:
    - slot: api
      asset: contoso-api       # an asset document that carries its own git `location`
```

A customer is therefore one small asset document plus this deployment; `strata.yaml` does not
grow. Put a binding shared by many deployments in a `partial` base, as for providers.

- **Rebind only**, and bindings merge by `slot` across `extends` (child wins), exactly as for
  providers.
- `strata validate` reports `asset_slot_unknown` (slot not declared by the workspace) as an error and
  `asset_binding_redundant` (binds the slot's own default) as a warning, with the same `partial`
  and `extends` handling as the provider codes. A bound document that does not exist is an
  `unknown_reference`.
- `strata deploy run` reports the bound document in `strata.assets.<slot>.asset`; it needs the
  directories `strata build run` produced for the same deployment.

## Tokens from the deployment

A workspace is shared by every deployment, so nothing written in it can say which one is running. These tokens read
it from the deployment that is:

- `${deployment:name}`: this document's `meta.name`
- `${deployment:tenant}`: its `spec.tenant`, including one inherited through `extends`
- `${deployment:kube_context}`, `${deployment:docker_context}`: its `spec.kube_context` and `spec.docker_context`,
  for a verify check that must reach the same cluster or engine (`[kubectl, --context, "${deployment:kube_context}",
  ...]`)

The usual use is a Terraform state key, `key: ${deployment:name}.tfstate`, in the workspace's backend (see
"Terraform backend" in [workspace](workspace.md)). `strata validate` checks them for every deployment that
reaches the document: `${deployment:tenant}` on a deployment without a tenant is an error (likewise a context it
does not set), as is any other key. A
`partial: true` base is judged only once a deployment extends it.

## Parameter sets

When many deployments differ only in their values (one per spoke, one per customer project), write the
deployment once as a base that reads `${param:<role>.<key>}`, and give each instance a
[parameter set](parameterset.md) and a short leaf:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke_deployment_base
  name_rule: spoke-${param:spoke.name}
spec:
  partial: true
  workspace: spoke
  tenant: ${param:spoke.tenant}
  environments:
    - solution-env
    - hub-${param:hub.name}-env
---
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke-acme-001
  params: { hub: hub-hub01, spoke: spoke-acme-001 }
spec:
  extends: spoke_deployment_base
```

The values are filled in when the solution loads, before anything runs, so they work in reference fields
(`tenant`, `environments`, `providers[].provider`) as well as free text. Each deployment is then checked as if it
had been written out in full; a problem is reported on the field you wrote, with the values that made it. Not in
`meta`, `spec.extends` or `spec.partial`. The rules are in [parameter sets](parameterset.md), a full example in
[How To: Write one deployment base for many spokes](../how-to/parameterised-deployment-bases.md).

## Choosing the target

Helm and Compose steps deploy to whatever cluster or Docker engine the caller's environment points at. Without a
named target, a wrong active context sends a production deployment somewhere else. Name it on the deployment:

```yaml
spec:
  extends: forge-base
  environments: [prd]
  kube_context: forge-prd      # Helm steps
  docker_context: hearth-prd   # Compose steps
```

Both fields work the same way: every call of that tool uses the named context, it is checked before the first step
runs, and the run stops before anything changes if it cannot be used. Without the field nothing changes at deploy,
and `strata validate` warns for a deployment with a step of that tool (`helm_kube_context_unset`,
`compose_docker_context_unset`). A partial base is not warned; the deployments extending it are, unless the base sets
the field. The value is a literal: a `${...}` token is a schema error. Strata never changes the caller's own
configuration: it does not set `KUBECONFIG` or `DOCKER_HOST`, write the kubeconfig, create a Docker context, or switch
the active context.

### Kubernetes (`kube_context`)

A Helm step deploys to a Kubernetes cluster through the kubeconfig the caller provides (`KUBECONFIG`, else
`~/.kube/config`). `kube_context` names a context in it:

- Every `helm` call of the deployment then runs against that context (`HELM_KUBECONTEXT`), whichever one is active:
  deploy, `--dry-run` preview, uninstall and destroy preview.
- Before the first step runs, strata checks that the kubeconfig defines the context. If it does not, the deploy stops
  with `helm_kube_context_missing`, listing the contexts it found. A `HELM_KUBECONTEXT` set to another context, or a
  `HELM_KUBEAPISERVER`, in the caller's environment stops it with `helm_kube_context_conflict`.
- Context names are local to a kubeconfig. Every machine that deploys (CI and laptops) must use the same context name
  for the cluster; `kubectl config rename-context` changes it.

### Docker (`docker_context`)

A Compose step runs `docker stack` against the engine Docker picks: `DOCKER_HOST`, else `DOCKER_CONTEXT`, else the
context chosen with `docker context use`. Create a context for each engine once
(`docker context create hearth-prd --docker host=ssh://deploy@hearth`) and name it:

- Every `docker` call of the deployment's Compose steps runs with `DOCKER_CONTEXT` set to it: `stack deploy`, the
  `--dry-run` preview (`stack config`), `stack rm` and the destroy preview (`stack services`).
- `docker stack deploy` runs with `--detach=false`: a step succeeds once its services have converged (their tasks
  run, and are healthy where the service has a `healthcheck`), as `stack rm --detach=false` waits for removal. A
  service that never converges keeps it waiting until `timeouts.apply` stops it, which fails the step.
- Before the first step runs, strata asks Docker (`docker context inspect`, which contacts no engine) whether the
  context exists. If not, the deploy stops with `compose_docker_context_missing`, listing the contexts Docker has. This
  also covers `--dry-run`, whose `docker stack config` never looks at the context.
- `DOCKER_HOST` silently overrides `DOCKER_CONTEXT`, so a set `DOCKER_HOST` stops the deploy with
  `compose_docker_context_conflict`, as does a `DOCKER_CONTEXT` naming another context. Replace a `DOCKER_HOST` in a
  workflow with a context created from the same host.
- `docker_context: default` is the local engine.

## Verifying a deploy

A deploy succeeds when its tool does: Terraform's `apply` succeeds once the cloud accepted the change, even if the
web app it created never starts. `verify` checks run after the change, wait for the workload to come up, and fail
the run when it does not ([ADR-0066](../decisions/0066-post-deploy-verification-is-a-deploy-phase.md)). They are
declared in two places:

- **A stage's `verify`**: checks for that step, run right after it is applied (or destroyed) and before the next
  step starts.
- **`spec.verify`**: checks for the deployment as a whole (an end-to-end request, a smoke transaction), run once
  after every step was applied (or destroyed).

```yaml
spec:
  stages:
    - step: apply_web
      verify:
        initial_delay: 30                 # the app needs time to start
        checks:
          - name: web-answers
            run: [pwsh, -File, checks/http-ok.ps1, "${output:apply_web.default_hostname}"]
            env:
              TOKEN: ${secret:HEALTH_TOKEN}
          - name: web-health              # the same, built in
            type: http
            url: https://${output:apply_web.default_hostname}/health
          - name: web-gone                # after a destroy: the site no longer answers
            when: [destroy]
            type: http
            url: https://${output:apply_web.default_hostname}/
            expect: down
  verify:                                 # after every step
    enforcement: warn                     # report only, while the check is new
    checks:
      - name: checkout-flow
        run: [python, checks/checkout_flow.py, "${output:apply_web.default_hostname}"]
```

- **A check** has a `type`, and the first line of what it reports is its message, secrets masked:

  | `type` | Fields | Answer |
  | ------ | ------ | ------ |
  | `script` (default) | `run`, `env`, `exit_codes` | the exit code: `binary` (default) 0 passes, anything else fails; `monitoring` (the Nagios/Icinga plugin convention) 0 passes, 1 warns, 2 fails, 3 is unknown. Out of `timeout`, unknown |
  | `http` | `url`, `method` (`GET`, `HEAD`, `OPTIONS`, `POST`), `headers`, `body` (with `POST`), `expect_status` (default `200`, or a non-empty list), `ca_file` or `insecure`, `expect` | passes when the status (after redirects) is in `expect_status`. An `application/health+json` answer's `status` decides instead: `pass`, `warn` or `fail`. No answer at all (refused, timed out) fails |
  | `tcp` | `host`, `port`, `expect` | passes when the connection opens; fails when it does not |
  | `helm` | `release`, `namespace`, `tests` (only these, by pod name), `env` | runs the chart's own tests, `helm test`, once, against the deployment's `kube_context`, with `--timeout` set to the check's `timeout`: 0 passes, anything else fails. Not retried: Helm already waited for the release to be ready. Not after a destroy |

  `expect: down` turns `http` and `tcp` around, for a check after a destroy: no answer passes, and so does an
  `http` status outside `expect_status`. A field of another type is refused. A check that cannot start (no such
  executable, not an `http(s)://` URL, a token that does not resolve, a `ca_file` that cannot be read) is unknown
  at once, not retried.
- **HTTPS:** the certificate and host name are checked against the system's certificate authorities. For a private
  CA or a self-signed certificate, set `ca_file`: a PEM file, relative to the solution root, whose authorities are
  trusted instead; the host name is still checked. `strata validate` reports a `ca_file` that does not exist or
  holds no certificate (`verify_ca_file_unusable`). `insecure: true` checks nothing, and the check's message ends in
  `(certificate not checked: insecure)`. A certificate that is not trusted fails the check, also with
  `expect: down`, since something still answers.
- **`POST`** is for a health endpoint that takes a query, such as GraphQL. `body` is sent as written, tokens
  resolved; set its `Content-Type` in `headers`. There is no `PUT`, `PATCH` or `DELETE`: a check must not change
  what it checks.
- **`run`** is an argument list (the executable on PATH or relative to the solution root, then its arguments),
  run from the solution root, never through a shell. Tokens in `run`, `env`, `url`, `headers`, `body`, `host`,
  `release` and `namespace` resolve
  as in a script provisioner, including `${output:STEP.KEY}`. In a stage's check, STEP is the stage's own step or
  one it depends on, directly or through others: those are the outputs the check is sure to have. In
  `spec.verify`, STEP is any step of the workspace. `strata validate` checks them all, and reports any other
  output as `verify_output_unavailable`. Put a secret in `env` or `headers`: an argument shows in the process
  list. A script also gets `STRATA_DEPLOYMENT`, `STRATA_STEP` (empty in `spec.verify`), `STRATA_CHECK`,
  `STRATA_ACTION` (`apply`, `destroy`, or `verify` under `deploy verify`) and `STRATA_ATTEMPT`.
- **The deployment's contexts:** a `script` or `helm` check reaches the cluster and engine the steps deployed to.
  With `kube_context`, it gets `HELM_KUBECONTEXT` (which `helm` reads) and `STRATA_KUBE_CONTEXT`; with
  `docker_context`, `DOCKER_CONTEXT` and `STRATA_DOCKER_CONTEXT`. A check's own `env` wins. `kubectl` reads no
  variable for its context, and `run` is never passed through a shell, so write
  `[kubectl, --context, "${deployment:kube_context}", ...]`.
- **Waiting** follows Kubernetes probes. Set on the block, and overridable per check:

  | Field | Default | Meaning |
  | ----- | ------- | ------- |
  | `initial_delay` | 10 | seconds before the first check (per check: before that check) |
  | `period` | 10 | seconds from one attempt's start to the next |
  | `timeout` | 30 | seconds one attempt may take; the command is then stopped |
  | `success_threshold` | 1 | consecutive passes needed |
  | `failure_threshold` | 30 | attempts that fail or are unknown before the check gives up |

  With the defaults a workload has about five minutes to come up. `timeouts.verify` on the stage bounds the whole
  phase. A check whose answer keeps flipping between pass and fail stops after `failure_threshold +
  success_threshold - 1` attempts and fails ("the answer keeps changing"). A `helm` check runs once.
- **What a result does**, as for the policy gates
  ([ADR-0067](../decisions/0067-verify-checks-use-the-gates-semantics.md)): a failed check is an error with
  `enforcement: deny` (default), so the run stops and is `partial`; with `enforcement: warn` it is only a warning.
  An unknown answer follows `on_unknown`: `block` (default), `warn` or `skip`. A warn is always a warning. Nothing
  is rolled back. A stage check's error fails its stage; a `spec.verify` error fails the run, not a stage.
- **When:** a check's `when` says after which action it runs: `[apply]` (default), `[destroy]`, or both. The
  `apply` checks run after each step of `deploy run` and `deploy apply`, then `spec.verify`'s once every step
  succeeded. The `destroy` checks run after each step of `deploy destroy --force`, then `spec.verify`'s once every
  step was destroyed: they confirm what was removed is gone (`expect: down`, or a script that exits 0 when it
  is). A destroy check's `${output:}` comes from the output store, read before the first step is destroyed.
  `spec.verify` is skipped, with a note, when `--stage`/`--scope` selects only some steps; after an apply, run
  `deploy verify` once the whole deployment is out. `--no-verify` skips every check for one run; `enabled: false`
  turns a block or one check off. `--dry-run`, `--smoke-test` and `deploy plan` only name the checks.
- **Across `extends`:** a block's fields deep-merge, and its `checks` merge by `name`: a child changes one of a
  base's checks field by field, adds its own, or turns one off with `enabled: false`. A child writes only what it
  changes, without the check's `type` or required fields:

  ```yaml
  spec:
    extends: shop-base
    stages:
      - step: apply_web
        verify:
          checks:
            - name: pods            # the base's kubectl check; this deployment has no kube_context
              enabled: false
            - name: web-health      # the base's http check, with a longer timeout
              timeout: 60
  ```

  A disabled check is not judged by `strata validate`: its tokens need not resolve and its files need not exist.
  The merged deployment's enabled checks are judged in full.
- **Recorded:** a `verify` phase in the audit manifest's stage and each stage check's answer, attempts, duration
  and message in `stages[].checks`; the `spec.verify` answers in the top-level `checks`; and `verified`: `true`
  when every check that ran answered pass or warn, `false` when one failed or could not answer (even with
  `enforcement: warn`), left out when no check ran. The metrics record count the checks (`checks_total`,
  `checks_failed`) and name the result (`verification`: `verified`, `verification_failed` or `not_verified`), and so
  do the events' `labels.verification` and the handover document. A run that failed only because of its checks
  has `error_category: verification`: the steps were applied, and the workload does not work.

### `strata deploy verify`

```bash
strata deploy verify DEPLOYMENT [--stage NAME] [--scope LABEL] [--output json]
```

Runs the `apply` checks on their own, against what is deployed now, and changes nothing: after a manual change, in a later
pipeline stage that deployed with `--no-verify`, or on a schedule the pipeline sets. Every selected stage's block
runs, then `spec.verify` (only when no `--stage`/`--scope` leaves a step out); a failure does not stop the others.
The `initial_delay`s are not waited, since nothing was just started; the retries are. `${output:STEP.KEY}` comes
from the workspace's `output_store`, so a check that reads an output needs one (`verify_outputs_unavailable`
otherwise). It writes no audit record. Exit codes: `0` no check ended in an error, `3` one did, `2` bad arguments.
`--output json` puts `verified`, each stage's `checks` and the deployment's `checks` in `data`.

Findings: `verify_check_failed`, `verify_check_unknown`, `verify_check_warning` from a check;
`verify_deployment_skipped` (info), `verify_nothing_declared`, `verify_outputs_unavailable`,
`verify_outputs_unusable`, `verify_no_steps` from the run; `verify_output_unavailable`, `verify_script_missing` and
`verify_ca_file_unusable` from `strata validate`.

`strata validate` also checks that the files of the solution an enabled check needs are there: the script a
`script` check runs (`run[0]` when it is a path, or the script an interpreter runs, as in `[pwsh, -File,
checks/x.ps1]` or `[python, checks/x.py]`), and an `http` check's `ca_file`. Only relative paths without tokens are
checked: a bare command is found on the `PATH` of the machine that deploys, and an absolute path names a file
there. The design is in [post-deploy-verification.md](../design/post-deploy-verification.md).

## Handover document

`spec.documentation` renders the handover document at the end of every successful `deploy run`: each step's outputs,
the secrets each step used with where to find them, and the images. It is the same document as
`strata deploy report --format markdown`, with that run's audit entry at the top. No secret value is ever in it:
every secret and every sensitive output shows as `********`.

```yaml
spec:
  documentation:
    sink: audit                     # the only sink: `_handover.md` next to the audit manifest
    template: docs/handover.md.j2   # optional; the built-in Markdown when unset
```

- `sink: audit` writes `_handover.md` next to `_manifest.json` and pushes it with every enabled `git` audit sink
  (Configuration `spec.audit.sinks[].git`). An integration sink sends events, not files, and does not carry it.
- `template` is a Jinja2 template relative to the solution root. It receives the data of
  `strata deploy report --output json`: `deployment`, `workspace`, `environments`, `steps`, `secrets`, `images`,
  `audit`, `generated_at`, and `MASK` (`********`). It runs sandboxed, and an unknown name fails.
- `strata validate` rejects it when the workspace has no `output_store` (`documentation_needs_output_store`), when
  no enabled git audit sink is configured (`documentation_needs_git_audit_sink`), or when the template is missing or
  uses a name the report does not have (`documentation_template_missing`, `documentation_template_invalid`).
- A render failure at the end of a deploy is a warning (`documentation_render_failed`), never a failed deploy:
  `strata deploy report` can render the document again.

The workflow, including handing a secret over with Password Pusher, is in
[deployment-handover.md](../how-to/deployment-handover.md).

## Notes

- `extends` composes *structure* (stages, workspace, provider bindings); `environments` composes
  *values* — a deployment can extend a base for its stage recipe while merging its own, different
  environment list.
- `versions`, `promotion`, `gates` are declared in the model (for CLI parity with real invocations)
  but have zero real production usage and no execution engine behind them yet.
- Run `strata values get prd-deployment KEY...` before `build run`/`deploy run` to see exactly
  what a deployment resolves.
