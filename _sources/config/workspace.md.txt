# Workspace (`kind: workspace`)

The composed "image" (ADR-0011): a static, versioned declaration of everything a solution could
deploy — providers, resources, topologies, namespaces, and the provisioning recipe that builds/
deploys them. A [`deployment`](deployment.md) (the "container instance") executes that recipe
against a specific environment; it never invents new tool bindings, only supplies runtime
parameters.

## Schema

- `spec.providers[]` — provider **slots**: each is a [`provider`](provider.md) document name, or
  `{slot, provider}` to give the slot a stable name distinct from its default document. IaC code
  addresses the slot (`strata.providers.<slot>`); a [`deployment`](deployment.md) can rebind it to
  another provider document of the same type — see "Provider slots" below
- `spec.assets[]` — asset **slots**: each is an [`asset`](asset.md) document name, or `{slot, asset}`
  to give the slot a stable name distinct from its default document. A step selects slots through
  its own `assets`, and a [`deployment`](deployment.md) can rebind a slot — see "Asset slots" below
- `spec.resources[]` — workspace-local resource instances, each:
  - `name` — the workspace-local instance name
  - `resource` — the [`resource`](resource.md) document it's built from (or `managed_by: provisioner`
    if fully externally managed)
  - `role` — matched against the topology type's expected component roles
  - `count` — number of instances (default 1)
  - `depends_on[]`, `firewalls[]`, `subnet` — workspace-specific gluing
  - `enabled` — whether this instance is included in the built artifact (default `true`)
- `spec.namespaces[]` — names of [`namespace`](namespace.md) documents
- `spec.topology[]` — inline topology groupings (type/components/namespaces/volumes) — see
  "Topology grouping" below. Inline since ADR-0028 (reverted from a standalone `kind: topology`
  document, ADR-0011) — not a list of names.
- `spec.provisioners[]` — tool definitions (`ProvisionerModel`): `name`, `tool` (terraform, ansible,
  helm, compose, script, ...), `source`, `backend` (terraform only), `properties` (ansible only),
  `script` (script only, see "Script provisioner" below),
  `integration` (which [`integration`](integration.md) document supplies version/auth), `output`
  (Jinja2 template escape hatch)
- `spec.execution[]` — the ordered recipe (`ProvisioningStepModel`): `name`, `provisioner`,
  `resources[]`/`namespaces[]` (direct references this step acts on), `topologies[]` (expands to
  that topology's own resources/namespaces), `dns_zones[]`/`firewalls[]`/`networks[]` (direct
  escape hatch for claiming one of those documents' `${output:...}` ownership without a
  resource/topology link), `depends_on[]` for step ordering. At least one of the six target-ish
  fields is required. A resource's own `firewalls`/`dns_zones`/`subnet.network` are claimed
  automatically once that resource is effectively in scope (direct or via `topologies[]`) — no
  need to also list them directly on the step.
  `assets[]` selects the asset slots this step needs (see "Asset slots").
- `spec.default_tags` — default cloud tags for resources created by this workspace
- `spec.cve_policy` — fail `build run` when the SBOM's CVE findings at or above `max_severity`
  exceed `max_count` (scanned with Trivy or Grype)
- `spec.checkov_policy` — fail `build run` on Checkov findings at or above `severity_gate`; a
  finding with no severity (open-source Checkov reports none) always counts
- `spec.release_gate_policy` — at deploy, ask a `release_gate` [integration](integration.md) for its
  verdict on `repository` at the deployed commit
- `spec.image_gate_policy` — at deploy, count an `image_findings` integration's open issues for every
  image in `sbom.json` against `max_severity`/`max_count`
- `spec.image_tag_policy` — fail `build run` and `deploy run` when an image the deployment runs has no
  tag, a floating tag such as `latest`, or a tag that does not match the shape you require (see "Image
  tag policy" below)

  Every policy has `enforcement` (`deny` | `warn`). The CVE, Checkov and gate policies also have
  `on_missing_data` (`skip` | `warn` | `block`, default `block`); `image_tag_policy` has none, because it
  reads only the solution. `strata policy list` shows what a deployment's workspace configures;
  `strata policy check` evaluates them on demand. See
  [how-to/security-gates.md](../how-to/security-gates.md).

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - slot: azure
      provider: azure-main
  namespaces:
    - hearth
  provisioners:
    - name: terraform-main
      tool: terraform
      source:
        remote: infra
        source_path: terraform/main
      backend:
        type: azurerm
        configuration:
          resource_group_name: tfstate-rg
          key: ${deployment:name}.tfstate
  resources:
    - name: web-storage
      resource: storage-account
      role: control-plane
    - name: web-vm
      resource: linux-vm
      role: worker
      count: 2
  execution:
    - name: provision-infra
      provisioner: terraform-main
      resources:
        - web-storage
        - web-vm
  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: web-storage
        - resource: web-vm
  default_tags:
    managed-by: strata
```

## Terraform backend

`spec.provisioners[].backend` (Terraform and OpenTofu only) holds the settings strata passes to `terraform init`,
one `-backend-config key=value` each. The `backend "azurerm" {}` block itself stays in the Terraform code, and
`type` is not sent to Terraform.

The values are resolved when `deploy run` starts the step, so they may contain `${var:}`, `${secret:}`,
`${feature:}` and `${output:STEP.KEY}` tokens, and two tokens for the deployment being run. A workspace is shared
by every deployment, so these are the way to give each deployment its own state without typing a key into every
deployment's environment:

| Token                  | Value                                                         |
| ---------------------- | ------------------------------------------------------------- |
| `${deployment:name}`   | the deployment's `meta.name`                                  |
| `${deployment:tenant}` | the deployment's `spec.tenant`, from the deployment or a base |

`${deployment:tenant}` on a deployment that has no tenant is an error (`strata validate` reports it, and so does
`deploy run`), not an empty string. Any other key, such as `${deployment:workspace}`, is rejected too. A
`partial: true` base is not judged: only the deployment that extends it is.

One rule for every deployment (Azure blob), with deployments `prd-contoso` and `prd-acme`:

```yaml
backend:
  type: azurerm
  configuration:
    resource_group_name: ${var:tf_state_resource_group}
    storage_account_name: ${var:tf_state_storage_account}
    container_name: tfstate
    key: ${deployment:name}.tfstate     # prd-contoso.tfstate, prd-acme.tfstate
```

A folder per tenant: a blob name may contain `/`. This workspace then only suits deployments that have a tenant.

```yaml
    key: ${deployment:tenant}/${deployment:name}.tfstate   # contoso/prd-contoso.tfstate
```

Other backends:

```yaml
backend:                                  # S3
  type: s3
  configuration:
    bucket: ${var:tf_state_bucket}
    region: ${var:tf_state_region}
    key: ${var:tf_state_prefix}/${deployment:name}/terraform.tfstate
    dynamodb_table: ${var:tf_lock_table}
---
backend:                                  # GCS takes a prefix, not a key
  type: gcs
  configuration:
    bucket: ${var:tf_state_bucket}
    prefix: ${deployment:name}
---
backend:                                  # local state, for a development workspace
  type: local
  configuration:
    path: .state/${deployment:name}.tfstate
```

Things to know:

- **Renaming a deployment moves its state.** The new name gives a new, empty state, so a rename becomes a
  migration (`terraform state` commands) or is never done.
- **Booleans.** A non-string value is converted with Python's `str()` before it reaches `-backend-config`, so a
  YAML `true` becomes `True`. Write a boolean setting as a quoted string (`"true"`).
- **Literal text.** To keep `${deployment:name}` as written, for a tool with its own `${...}` syntax, write
  `$${deployment:name}`.
- The tokens are not limited to the backend: any field strata resolves at deploy time (a module's values, Terraform
  variables) can use them. They are not allowed in an [`environment`](environment.md) store's own `value`.
  The design, and why it is a token rather than a default key, is in
  [deployment-tokens.md](../design/deployment-tokens.md).

## Script provisioner

`tool: script` runs a `.ps1`, `.sh`, `.bash`, `.py` or `.bat` file as a step. `script` is required for this tool and
rejected for any other built-in one, and `source` is required as for every tool: `source.source_path` is the directory
that is materialised, and `script.file` is relative to it.

```yaml
provisioners:
  - name: secrets-script
    tool: script
    source:
      source_path: scripts/secrets
    script:
      file: secrets.py
      args: [--action, reload]
      plan_args: [--action, plan]
      destroy_args: [--action, revoke, --records, "${var:revoke_records}"]
      values: [VAULT_TOKEN]
```

| Field          | Used by                                  | When unset                                                    |
| -------------- | ---------------------------------------- | ------------------------------------------------------------- |
| `file`         | every phase                              | required                                                      |
| `args`         | `deploy run`, the apply                  | the script gets no arguments                                  |
| `plan_args`    | `deploy run --dry-run` and `deploy plan` | a preview prints `would run: ...` and executes nothing        |
| `destroy_args` | `deploy destroy --force`                 | the step cannot be destroyed, and `deploy destroy` refuses it |
| `values`       | the environment variable `STRATA_VALUES` | the script receives every resolved value                      |

- Every argument can use value tokens. The path of the rendered `output.template`, when the provisioner sets one, is
  appended to `args` and `plan_args` as the last argument.
- A script has to guard itself: strata cannot check that it changes nothing when run with `plan_args`.
  `STRATA_PHASE` (`plan`, `apply` or `destroy`) tells it which phase it is in.
- `values` lists keys: variables, secrets, features, and `<step>.<key>` outputs. `STRATA_VALUES` then holds those and
  `deployment:name` and `deployment:tenant`. `strata validate` reports a key nothing declares. It limits
  `STRATA_VALUES` only: arguments can still use any token.
- A script passes values to later steps by writing a JSON object to the file named by `STRATA_OUTPUT_FILE`;
  `_sensitive` lists the keys never recorded in the `output_store`.
- `deploy apply` refuses a script step: it writes no saved plan. Use `deploy run`.

A worked example, with the script, is in [how-to/script-provisioner.md](../how-to/script-provisioner.md).
The design is in [script-provisioner.md](../design/script-provisioner.md).

## Image tag policy

`spec.image_tag_policy` is an opt-in guard rail over the images `strata deploy images` lists: the artifacts a
deployment reaches and the literal images of its enabled module services, each with the tag after any
[`version`](version.md) pin. Leave the field out and any tag is accepted, including none. `image_tag_policy: {}`
is already a useful policy: every image needs a tag, and the mutable aliases are rejected.

```yaml
spec:
  image_tag_policy:
    enforcement: deny              # deny (default) | warn
    origins: [artifact]            # artifact | module-service; default: both
    require_tag: true              # default true
    reject_tags: [latest, main]    # default: latest, main, master, dev, develop, staging, edge, nightly, canary
    tag_pattern: '^\d+\.\d+\.\d+$'  # a tag must match it (searched, so anchor it); a digest is not checked
    require_pin: false             # the tag must come from a kind: version pin (or --pin)
    rings: [acc, prd]              # only deployments whose version document is in these rings; default all
    allow:
      - name: redis                # the image's name as `strata deploy images` lists it
        reason: third-party image, covered by cve_policy
        expires: 2026-12-31        # optional ISO date, honoured through that day
```

| Rule          | An image fails when                           | Finding code         |
| ------------- | --------------------------------------------- | -------------------- |
| `require_tag` | it has no tag and no pin supplies one         | `image_without_tag`  |
| `reject_tags` | its tag is in the list, ignoring case         | `image_floating_tag` |
| `tag_pattern` | its tag does not match the regular expression | `image_tag_pattern`  |
| `require_pin` | its tag does not come from a version pin      | `image_not_pinned`   |

- A digest (`sha256:...`) is immutable, so it passes `reject_tags` and `tag_pattern`. It still has to satisfy
  `require_tag` and `require_pin`.
- A tag that merely is not a semantic version, such as a build number (`env_sbx_20260907.11`), is not floating.
  `reject_tags` is an explicit list; use `tag_pattern` to be stricter. Set `reject_tags: []` to reject none.
- An image with no tag gets one finding, not also `image_not_pinned`, unless `require_tag` is `false`.
- Each finding names the image, its origin and what uses it, and says where to put the tag:
  `spec.pins.artifacts.<name>` of a version document, or `spec.pins.images.<name>` for a module service.
- `deny` makes a finding an error and stops the run; `warn` makes it a warning.
- An `allow` entry exempts one image from every rule. The `reason` is required. Once `expires` has passed, the
  entry no longer exempts the image and the finding says so.
- A deployment whose version document has a ring outside `rings` is not judged, and reports one info finding. A
  deployment with no ring is always judged. A workspace is shared by every ring of a product, so `rings` is how
  acc and prd can be strict while dev floats.

Where it runs:

| Command                | Behavior                                                                                                                                                    |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata build run`     | Judged first, before anything is cleaned or written, so a `deny` breach leaves the build path as it was. Also under `--dry-run`.                            |
| `strata deploy run`    | Judged again before any value is resolved, because `--pin` given only to `deploy run` can change the tag. Also under `--smoke-test`, which records nothing. |
| `strata deploy images` | The policy replaces the plain missing-tag warning, and `deny` exits 3, so an image copy step at the start of a pipeline fails first.                        |
| `strata policy check`  | Evaluates it offline and records the result. `strata policy list` shows it.                                                                                 |
| `strata validate`      | Not evaluated: the effective tag depends on the deployment and on `--pin`.                                                                                  |

The result is recorded in `policy_results.json` as `image_tag_policy` and ends up in the audit manifest. The
`deploy run` entry replaces the one `build run` wrote.

The policy judges the images strata knows. For a Compose module and for an artifact that is what runs. For a Helm
module, strata does not write a service's `image` into the chart's values, so the chart decides what runs: the
policy judges the declared image, and an image that only the chart knows is not judged. Set a Helm image through
the module's `configuration`, in the chart's own key shape. See
[helm-image-delivery.md](../work/helm-image-delivery.md).

## Provider slots

A `spec.providers[]` entry is a named **slot** with a default provider document:

```yaml
providers:
  - slot: azure              # stable key — IaC code addresses strata.providers.azure
    provider: azure-main     # default Provider document (westeurope)
  - azure-shared             # bare name = slot and document share the name
```

- `slot` is unique within the workspace; two slots may point at the same document.
- A bare string is shorthand for `{slot: <name>, provider: <name>}`, so a workspace that never
  rebinds anything reads exactly as before and keeps its `strata.providers.<name>` keys.
- The same workspace can target another region or account without its IaC code changing: a
  [`deployment`](deployment.md) rebinds the slot (`spec.providers[]` there). Prefer an explicit
  slot name (`azure`) over one named after a specific region's document (`azure-main`) when you
  expect to rebind — otherwise `strata.providers.azure-main` would silently point at another
  region.

## Asset slots

A `spec.assets[]` entry is a named **slot** with a default [`asset`](asset.md) document, and an
execution step selects the slots it needs:

```yaml
assets:
  - slot: api                # stable key — steps select it, IaC code reads local.strata_assets.api
    asset: api-default       # default Asset document
  - openapi                  # bare name = slot and document share the name
execution:
  - name: publish_api
    provisioner: terraform_apim
    resources: [apim]
    assets: [api]            # selects the slot
```

- `slot` is unique within the workspace; two slots may point at the same document. A step
  selects a **slot**, never a document name directly.
- A bare string is shorthand for `{slot: <name>, asset: <name>}`, so a workspace that never
  rebinds anything reads exactly as before.
- Only slots a step selects are materialised and projected, so an unused slot costs nothing.
- A [`deployment`](deployment.md) rebinds the slot to another asset document, so one workspace can
  serve many customers whose files live in different repositories.

## Topology grouping

Each `spec.topology[]` entry is a named grouping of resources and namespaces that conceptually
belong together (e.g. "the AKS cluster + its blob store + its key vault") — for documentation,
diagramming, and workload placement. It deliberately carries no provider/provisioner binding —
which tool builds/deploys these resources is a separate concern (`spec.execution`/`provisioners`
above): a single provisioner can span several groupings, and a single grouping can be built by
several different provisioners acting on different subsets of it (ADR-0011).

- `name` — unique grouping name within this workspace
- `type` — the topology type (must match a [topologyconfig.md](topologyconfig.md) document's
  `meta.name`)
- `components[]` — each `{resource, modules}`: a reference to this workspace's own
  `spec.resources[].name` (checked at Phase 1 — only possible because the grouping is inline now,
  ADR-0028), optionally with application code (`modules[]`, same shape as
  [namespace.md](namespace.md)'s module references) attached directly to that resource (e.g. an
  Azure Function App's code onto its Function App resource)
- `namespaces[]` — each `{namespace}`: a reference to this workspace's own `spec.namespaces[]`
- `volumes[]` — topology-level storage volumes

When multiple modules are attached to one component and more than one is enabled, exactly one
must be marked as the `main` slot.

## Notes

- Three sibling keys answer three different questions: `providers` (where — which account, as
  rebindable slots), `provisioners` (with what — tool definitions), `execution` (what runs, in
  what order).
- `execution` names steps ordered by `depends_on`, not a numeric `priority` (ADR-0011) — `strata
  deploy run` executes steps in that dependency order.
- A [`deployment`](deployment.md)'s `stages[]` reference `execution[].name` for runtime parameters
  (timeouts, health checks, enabled/disabled) — they never redefine what runs.
