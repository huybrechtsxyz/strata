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
  finding with no severity (open-source Checkov reports none) always counts. `config_file` names a
  Checkov config file (e.g. `.checkov.yaml`) relative to the root of the repository each scanned
  provisioner or Helm module comes from; `build run` copies it to `checkov-config/` in the build path
  and passes it as `--config-file`. A repository without that file is handled by `on_missing_data`.
  Without `config_file` strata passes none, and Checkov may still pick up a `.checkov.yaml` from the
  scanned directory or the directory strata runs in
- `spec.release_gate_policy` — at deploy, ask a `release_gate` [integration](integration.md) for its
  verdict on `repository` at the deployed commit
- `spec.image_gate_policy` — at deploy, count an `image_findings` integration's open issues for every
  image in `sbom.json` against `max_severity`/`max_count`
- `spec.image_tag_policy` — fail `build run` and `deploy run` when an image the deployment runs has no
  tag, a floating tag such as `latest`, or a tag that does not match the shape you require (see "Image
  tag policy" below)
- `spec.sbom` — what `build run` adds to `sbom.json`: `render_charts: true` renders every Helm chart, local
  or pinned from a registry, with `helm template` and lists the images it deploys, so the CVE and image gates and
  `image_tag_policy` check them (see
  [how-to/sbom-generation.md](../how-to/sbom-generation.md#helm-images))
- `spec.provisioner_lock_policy` — fail `build run` when a Terraform or OpenTofu provisioner has no
  `.terraform.lock.hcl`, or one that does not lock every provider it declares (see "Provisioner lock policy"
  below)

  Every policy has `enforcement` (`deny` | `warn`). The CVE, Checkov and gate policies also have
  `on_missing_data` (`skip` | `warn` | `block`, default `block`); `image_tag_policy` and
  `provisioner_lock_policy` have none, because they read only the solution and the build path. `strata policy list` shows what a deployment's workspace configures;
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
`${feature:}`, `${output:STEP.KEY}` and [`${artifact:NAME.FIELD}`](artifact.md) tokens, and two tokens for the
deployment being run. A workspace is shared
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

## Output store

`spec.output_store` (a backend type, such as `azure-appconfig` or `azure-blob`) or `spec.output_store_integration`
(an Integration document) keeps each step's outputs after `deploy run`, so a later run can resolve
`${output:STEP.KEY}` for a step it does not run. `spec.output_store_max_age` (seconds) rejects a record older than
that; `deploy run --output-max-age` overrides it for one run. The backend must support compare-and-swap.

- Each step has its own key, `strata/step-outputs/<deployment>/<step>`, holding when it was collected and its
  non-sensitive values. A sensitive output is never stored. Destroy marks the step's key as destroyed.
- A backend can limit the size of one key. `azure-appconfig` allows 10 KB, counting the key itself; `azure-blob` has
  no practical limit. Above 8,000 bytes, `deploy run` warns (`output_store_record`); above the backend's limit, the
  step's outputs are not recorded and `deploy run` warns with the step, its size and the limit. A step's deploy
  never fails over this. To fix it, make the largest outputs smaller, mark them sensitive, or use `azure-blob`.
- Next to it, `strata/deployment-report/<deployment>/<step>` holds what only `strata deploy output` and `strata deploy
  report` read: the names of the step's sensitive outputs, the `description` of each output from the root module's
  `output` blocks, and the secrets the step used, each with its store, location, `description` and the environment
  file that declares it. Never a value. Helm and Compose steps get one too (secrets only: they have no outputs).
- `strata/deployment-audit/<deployment>` holds the last successful deploy's audit summary (who, when, the commit,
  the audit record's id), which `strata deploy report` shows at the top. Above the backend's limit, descriptions are shortened to 200 characters, then left out
  (`report_store_record` warning); names are always kept. `strata build run` sizes this record from the rendered
  `.tf` files and warns early (`report_record_size`). A failed write is a warning, never a failed deploy.
- `strata deploy report DEPLOYMENT` shows the handover document: outputs, every secret a step used by location
  (`********` for the value, always), and the images. `output_store` is a prerequisite for it, as for `deploy output`.
  A location comes from the resolver's own settings where the deploy ran (`AZURE_KEYVAULT_URL`, `INFISICAL_*`), so
  it says where the deploy actually read the secret.
- `strata deploy output DEPLOYMENT [STEP | STEP.KEY]` shows the recorded outputs, like `terraform output`; it needs
  `output_store` and access to it, but no Terraform state. A sensitive output shows as `<sensitive>`. A record older
  than `output_store_max_age` is shown with a warning, not refused.
- Records written by strata 2.5 and earlier sit under one key per deployment, `strata/deployment-outputs/<deployment>`.
  They are still read for a step that has no key of its own, and while that key exists each deploy updates it too,
  so an older strata version reading it never gets an outdated value. A later release stops both.

The design is in [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) and
[ADR-0057](../decisions/0057-output-record-one-key-per-step.md).

## Image tag policy

`spec.image_tag_policy` is an opt-in guard rail over the images `strata deploy images` lists: the artifacts a
deployment reaches and the literal images of its enabled module services, each with the tag after any
[`version`](version.md) pin. Leave the field out and any tag is accepted, including none. `image_tag_policy: {}`
is already a useful policy: every image needs a tag, and the mutable aliases are rejected.

```yaml
spec:
  image_tag_policy:
    enforcement: deny              # deny (default) | warn
    origins: [artifact]            # artifact | module-service | chart; default: all three
    require_tag: true              # default true
    reject_tags: [latest, main]    # default: latest, main, master, dev, develop, staging, edge, nightly, canary
    tag_pattern: '^\d+\.\d+\.\d+$'  # a tag must match it (searched, so anchor it); a digest is not checked
    require_pin: false             # the tag must come from a kind: version pin (or --pin)
    require_digest: false          # the image must carry a digest (name@sha256:... or name:tag@sha256:...)
    rings: [acc, prd]              # only deployments whose version document is in these rings; default all
    on_unknown_image: warn         # a Helm module that declares no image: skip | warn (default) | block
    allow:
      - name: redis                # as `strata deploy images` lists it, or REFERENCE/CONTAINER for a chart's image
        reason: third-party image, covered by cve_policy
        expires: 2026-12-31        # optional ISO date, honoured through that day
```

| Rule             | An image fails when                              | Finding code           |
| ---------------- | ------------------------------------------------ | ---------------------- |
| `require_tag`    | it has no tag or digest and no pin supplies one  | `image_without_tag`    |
| `reject_tags`    | its tag is in the list, ignoring case            | `image_floating_tag`   |
| `tag_pattern`    | its tag does not match the regular expression    | `image_tag_pattern`    |
| `require_pin`    | its tag does not come from a version pin         | `image_not_pinned`     |
| `require_digest` | it has no digest                                 | `image_without_digest` |

- A digest (`sha256:...`) is immutable, so a digest alone satisfies `require_tag` and is never judged by
  `reject_tags` or `tag_pattern`. With `tag@digest` ([artifact](artifact.md#tag-and-digest)), those two judge
  the tag part, so `latest@sha256:...` is still a floating tag name. Every image still has to satisfy
  `require_pin`, except an image a chart runs (below).
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

| Command                | Behavior                                                                                                                                                                                                                                            |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata build run`     | Judged at the end, after `sbom.json` is written, so it also judges the images rendered charts run (`sbom.render_charts`). A `deny` fails the build with its output on disk. Under `--dry-run`, only the declared images, since nothing is rendered. |
| `strata deploy run`    | Judged again before any value is resolved, because `--pin` given only to `deploy run` can change the tag. The images the build's rendered charts run are judged too, from its `sbom.json`. Also under `--smoke-test`, which records nothing.        |
| `strata deploy images` | The policy replaces the plain missing-tag warning, and `deny` exits 3, so an image copy step at the start of a pipeline fails first. It reads no build, so it judges the declared images only.                                                      |
| `strata policy check`  | Evaluates it offline and records the result. With a build at `--build-path`, it also judges that build's rendered chart images and says which `sbom.json` they came from. `strata policy list` shows it.                                            |
| `strata validate`      | Not evaluated: the effective tag depends on the deployment and on `--pin`.                                                                                                                                                                          |

The result is recorded in `policy_results.json` as `image_tag_policy` and ends up in the audit manifest. The
`deploy run` entry replaces the one `build run` wrote. Because `build run` judges at the end, a pipeline that
wants the answer before it clones and renders runs `strata deploy images` or `strata policy check` first.

The policy judges the images strata knows. For a Compose module and for an artifact that is what runs. For a Helm
module, strata does not write a service's `image` into the chart's values, so the chart decides what runs. Set a
Helm image through the module's `configuration`, in the chart's own key shape. See
`helm-image-delivery.md` (work doc).

- **With [`sbom.render_charts`](../how-to/sbom-generation.md#helm-images)** the policy also judges every image the
  rendered charts run, sidecars and init containers included, read from the build's `sbom.json`. That is origin
  `chart`; a workspace that lists `origins` must add `chart` to get them.
  - Each is named `REFERENCE/CONTAINER`, the module reference in the namespace and the container, for example
    `whoami/shipper`. That is the name an `allow` entry uses.
  - `require_pin` does not apply: strata cannot pin an image inside a chart. The chart's version pin fixes it.
  - The fix the finding gives is to set the tag in the chart's values through the module's `configuration`, or
    to pin a chart version that ships the tag you want.
  - A declared image the chart also runs is judged once, as delivered. A Helm module whose chart rendered at
    least one image is no longer reported by `on_unknown_image`.
  - One info line, `image_tag_policy_rendered_images`, says how many images came from which `sbom.json`, and when
    it was written. At deploy and in `policy check` that tells you which build was judged.
  - `strata deploy images` reads no build, so it still judges the declared images only.
- **A declared Helm image** is still judged, by the same rules. Without `render_charts`, or when the chart was not
  rendered, its finding says "declared only, the chart decides which image runs". `strata deploy images` shows it
  as `DELIVERED no (helm module: the chart decides)`.
- **A Helm module that declares no service `image` or `artifact`** has images that only the chart knows.
  - `on_unknown_image` decides what happens, apart from `enforcement`. The finding is `image_unknown_to_strata`.
    - `warn` (the default) reports it.
    - `block` stops the run.
    - `skip` keeps it as an info finding, shown only in verbose and JSON output.
  - The policy's `rings` and `origins` apply: the module counts as a `module-service` image.
  - `allow` does not exempt a module. To have its images judged, declare them under the module's `services`.
  - Without a policy, `strata deploy images` still lists such a module under "Known only to Helm".

## Provisioner lock policy

Terraform records the provider versions it chose in `.terraform.lock.hcl`, next to the root module's `*.tf` files.
strata reads that file to list each provider in `sbom.json` at its real version; without it a provider is listed
without a version, or not at all when the code uses it without declaring it in `required_providers`, and a scanner
cannot check it. `build run` then prints an `info` line naming the folder and the providers, and never fails.

`spec.provisioner_lock_policy` makes a missing or incomplete lock file a finding:

```yaml
spec:
  provisioner_lock_policy:
    enforcement: deny              # deny (default) | warn
    rings: [acc, prd]              # only deployments whose version document is in these rings; default all
    allow:
      - provisioner: legacy-infra  # a name in spec.provisioners
        reason: vendored root, providers pinned exactly in required_providers
        expires: 2026-12-31        # optional ISO date, honoured through that day
```

- Every `terraform` and `opentofu` provisioner this build materialised is judged, against the lock file in its
  source folder. Child modules are covered by that file.
- It fails when the source needs providers (it declares `required_providers` anywhere, or its root has a
  `provider`, `resource`, `data` or `module` block) and the lock file is missing, or when the lock file does not
  lock a declared provider. Both are finding `provisioner_lock_missing`, which names the providers and says to run
  `terraform init` and commit the lock file.
- A root with only outputs and locals has nothing to lock and passes.
- `deny` makes the finding an error and fails `build run` (exit 3); `warn` makes it a warning. With the policy set,
  the `info` line is not printed.
- `allow` exempts a provisioner and needs a `reason`; `rings` works as for the image tag policy. An `allow` entry
  that names no declared provisioner fails validation.

| Command               | Behavior                                                                                    |
| --------------------- | ------------------------------------------------------------------------------------------- |
| `strata build run`    | Judged after the SBOM, beside `cve_policy` and `checkov_policy`. Not under `--dry-run`.     |
| `strata policy check` | Evaluates it offline against `--build-path` and records the result. `policy list` shows it. |
| `strata deploy run`   | Not evaluated: deploy uses the built sources.                                               |
| `strata validate`     | Not evaluated: remote sources are not materialised yet.                                     |

The result is recorded in `policy_results.json` as `provisioner_lock_policy` and ends up in the audit manifest. The
design is in [provisioner-lock-policy.md](../design/provisioner-lock-policy.md).

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
