# Tags, Labels, `configuration` and `custom` — Which Kind Gets What

- Status: partially-implemented
- Date: 2026-09-22
- Related: [ADR-0003](0003-provider-model-design-decisions.md) (found v1's
  `custom`/`default_tags` validated-then-silently-dropped, and introduced the
  `properties`/`configuration`/`custom` three-way split),
  [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (the vestigial-field
  pattern)

## Context and Problem Statement

Four field names were applied inconsistently across the kinds, and one of
them was actively misleading.

**`meta.tags` is not cloud tags.** v1 types it `list[Any]` and threads it
into the Jinja render context as `"tags": resource.tags or []`. Real cloud
tags (Azure/AWS) are `dict[str, str]` key-value pairs used for billing and
governance. A list cannot express them. So despite the name, `meta.tags` is
a strata-internal categorization list.

**The one dict-shaped candidate was dead.** v1's
`ProviderSpecModel.default_tags: dict[str, str]` *is* shaped like real cloud
tags — "Default tags to apply to all resources created by this provider".
Grepping v1's `terraform_builder.py` for `default_tags` returns **nothing**.
Validated on parse, never rendered. Same defect class ADR-0003 already found
for `custom`.

Net: neither v1 nor v2 had a working mechanism for actual cloud resource
tags, while `meta.tags` sat there looking like one.

Separately, `configuration` and `custom` were present on some kinds and
absent from others with no stated rule, and the Kubernetes-native kinds had
no way to express `metadata.labels` at all.

## Decision

**Four distinct concepts, named distinctly:**

| Field | Shape | Meaning | Validated by strata? |
|---|---|---|---|
| `meta.tags` | `list[Any]` | strata-internal categorization/docs | no |
| `default_tags` / `custom_tags` | `dict[str, str]` | real cloud provider tags | no |
| `default_labels` / `custom_labels` | `dict[str, str]` | Kubernetes/Compose labels | no |
| `properties` | typed model | strata-steering fields | **yes** |
| `configuration` | `dict[str, Any]` | raw provisioner passthrough | no |
| `custom` | `dict[str, Any]` | user data for scripts/extensions | no |

**Placement follows what the kind actually is:**

1. **Genuinely provisioned cloud resources get `default_tags` (required) +
   `custom_tags` (optional)**: `Resource`, `Firewall` (spec-level — one
   ruleset per file), `Dns` (on `DnsZoneModel` — a file may declare several
   zones), `Network` (on `NetworkDefinitionModel` — likewise). Required,
   because these are the governance-bearing resources where a missing
   cost-centre or owner tag is a real operational problem.

2. **Kubernetes-native kinds get `default_labels` + `custom_labels`
   instead**: `Module` (labels on the workload it generates — Pods/
   Deployments, or Compose service labels) and `Namespace` (labels on the
   generated Namespace object itself, e.g. for selectors, network policies,
   `istio-injection`). Cloud tags do not apply: Azure cannot tag a Pod, and
   a Module has no cloud identity of its own — the Resource it deploys onto
   is what gets tagged. AKS node pools, if ever modelled individually, are
   infrastructure and would be `Resource`s, so the rule holds.

3. **`Provider` gets optional `default_tags` only, no `custom_tags`** — it
   is a provider-wide default, not an individually tagged resource.
   `Workspace` likewise gets an optional solution-wide `default_tags` (e.g.
   `managed-by: strata`), useful when a workspace spans multiple providers.

4. **Nothing tag-like on**: `Topology` (pure grouping, nothing provisioned),
   `Integration` (a connection to an external system), `ProviderConfig`/
   `TopologyConfig` (type registries, never deployed), `Solution`.

5. **`configuration` and `custom` added wherever they were missing and
   meaningful** — `Firewall`, `Dns` (per zone), `Network` (per network),
   `Namespace`, `Module` (`custom`), `Integration`. Not added to `Topology`
   (no tool binding to pass configuration to — ADR-0011) or to the two
   registries.

6. **Strata does not enforce a maximum tag count.** Provider and
   resource-type limits vary too much to bake into the schema; keeping
   `default_tags` + `custom_tags` within the target provider's limit is the
   resource author's responsibility. Stated explicitly in the field
   descriptions so the omission reads as deliberate.

7. **Intended merge order** (once a builder consumes it):
   `Workspace.default_tags` → `Provider.default_tags` →
   `Resource.default_tags`/`custom_tags` → `WorkspaceResourceModel`
   override, closest wins.

## Consequences

- Good: "tags" is no longer one word meaning two incompatible things. The
  list-shaped categorization field and the dict-shaped cloud-tag fields
  coexist with names that say which is which, and every `default_tags`
  description states the distinction.
- Good: the Kubernetes-native kinds can express labels, which is the
  correct vocabulary for what they actually produce — rather than being
  given cloud tags that no API would accept.
- Good: placement now follows a stated rule ("is this thing actually
  provisioned, and by which system") rather than case-by-case habit.
- Cost: `default_tags` being **required** on Resource/Firewall/Dns/Network
  broke 8 test fixtures. Deliberate — the alternative is optional governance
  tags, which in practice means absent governance tags.
- Neutral: all of these fields are inert until a builder consumes them. That
  is the same state ADR-0003 documented for `custom`, and the field
  descriptions say so rather than implying they work.

## Remaining Work

- `WorkspaceResourceModel.configuration`/`.custom`/`.labels`/`.tags` are
  still present but were found to be dead in v1 — tracing
  `platform_builder.py` shows only `firewalls`, `role` and `count` are
  threaded from the workspace glue layer into
  `PlatformResourceModel.from_resource_model()`; the artifact's
  configuration/custom/labels/tags come exclusively from the `Resource`
  document. Either they gain real merge semantics when a builder is written,
  or they should be dropped. Not resolved here.
