# Diagram Configuration

Defines how live workspace data is turned into a [Mermaid](https://mermaid.js.org/) diagram.
A `diagram` document names the data it needs, then renders it through a Jinja2 template into
Mermaid source — which the CLI prints and the VS Code extension draws.

See [ADR-0034](../decisions/0034-diagram-visualization-in-vscode-extension.md) for the design
rationale.

## When to Use

Use the `diagram` kind when you want to:

- Visualise workspace data (topology, drift, promotion flow, SBOM, …) without writing a script
- Keep a diagram definition in version control so everyone sees the same picture
- Produce a Mermaid diagram type strata does not ship a built-in for

Strata does not replace Mermaid — it makes Mermaid easier to point at your workspace. The
template emits arbitrary text, so every Mermaid diagram type is expressible; there is no
schema ceiling.

## Render Pipeline

```text
spec.sources[]  ->  Jinja context  ->  spec.template  ->  Mermaid  ->  SVG
```

1. Each entry in `spec.sources` is fetched from the workspace and bound into the render context
   under its `as` name (defaulting to the source `type`).
2. `spec.template` is rendered against that context and must emit valid Mermaid source.
3. If `spec.template` is omitted, a template is generated from `spec.layout` and `spec.style`.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: <diagram_name>      # Required: ^[a-z0-9][a-z0-9_-]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
  tags: []
spec:
  sources:                  # Optional: omit for a purely static diagram
    - type: <source_type>   # Required: see Source Types below
      as: <context_name>    # Optional: Jinja binding name, defaults to `type`
      filter:               # Optional: narrow the source to a subset
        <key>: <value>
  template: |               # One of: Jinja2 template emitting Mermaid source
    flowchart TD
    {% for node in topology.nodes %}
      {{ node.id }}["{{ node.label }}"]
    {% endfor %}
  layout:                   # One of: hints used to generate a template
    type: <mermaid_type>    # Default: flowchart
    direction: <direction>  # Optional: TD, LR, BT, RL
  style:                    # Optional: only used when `template` is omitted
    color_by: <field>
    group_by: <field>
    highlight:
      - condition: <expr>
        token: <token>
```

## Top-level Fields

| Field           | Type   | Required | Description                                                           |
| --------------- | ------ | -------- | --------------------------------------------------------------------- |
| `spec.sources`  | array  | No       | Workspace data sources bound into the Jinja context.                  |
| `spec.template` | string | one of   | Jinja2 template rendering the context into Mermaid source.            |
| `spec.layout`   | object | one of   | Layout hints used to generate a template when `template` is omitted.  |
| `spec.style`    | object | No       | Styling hints used to generate a template when `template` is omitted. |

> At least one of `spec.template` or `spec.layout` must be set. When both are present,
> `spec.template` wins and `layout`/`style` are ignored.

## spec.sources Fields

| Field    | Type   | Required | Description                                                                       |
| -------- | ------ | -------- | --------------------------------------------------------------------------------- |
| `type`   | string | Yes      | Workspace data source to fetch — see Source Types below.                          |
| `as`     | string | No       | Name bound in the Jinja context. Defaults to `type`.                              |
| `filter` | object | No       | Narrow the source to a subset, e.g. `environment: prd` or `severity: [critical]`. |

**`as` naming rule:** must match `^[a-z_][a-z0-9_]*$` — lowercase letters, digits, and
underscores only. This is stricter than `meta.name`, which permits hyphens, because a hyphen is
not a valid Jinja identifier: `{{ my-source }}` parses as a subtraction, not a variable lookup.

Every source in a diagram must bind to a **distinct** name, since the bindings become context
keys. Use `as` to disambiguate when listing the same `type` twice with different filters:

```yaml
sources:
  - type: resources
    as: prd
    filter:
      environment: prd
  - type: resources
    as: acc
    filter:
      environment: acc
```

## Source Types

Every `DiagramSourceType` is implemented. See
[ADR-0034](../decisions/0034-diagram-visualization-in-vscode-extension.md) for the rollout history.

| Type           | Status      | Provides                                                                                                                                                                                                                                                              |
| -------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `topology`     | Implemented | The workspace's logical resource graph — resources, modules, namespaces, networks, and the `depends_on`/`runs`/`subnet`/`firewall` edges between them, plus provisioner topology groupings.                                                                           |
| `files`        | Implemented | YAML file reference graph (which document references which).                                                                                                                                                                                                          |
| `resources`    | Implemented | Just the `resource`-kind nodes of the topology graph (includes dangling `depends_on` targets).                                                                                                                                                                        |
| `modules`      | Implemented | Just the `module`-kind nodes of the topology graph.                                                                                                                                                                                                                   |
| `namespaces`   | Implemented | Just the `namespace`-kind nodes of the topology graph.                                                                                                                                                                                                                |
| `network`      | Implemented | Networks declared in the `kind: network` document(s) `spec.networks[].file` references — subnets, address space, and peering edges.                                                                                                                                   |
| `firewalls`    | Implemented | Firewall rule sets referenced by `spec.firewalls[].file` — one node per referenced ruleset, summarised (allow/deny/default counts).                                                                                                                                   |
| `dns`          | Implemented | DNS zones declared in the document(s) `spec.dns_zones[].file` references.                                                                                                                                                                                             |
| `stages`       | Implemented | The resolved deployment's `spec.stages[]` — declared only, no live run status — with `depends_on` edges.                                                                                                                                                              |
| `environments` | Implemented | The resolved deployment's `spec.environments[]` documents, deduplicated by name (a file referenced under two `scope` values is one node).                                                                                                                             |
| `tenants`      | Implemented | Every `kind: tenant` document found anywhere in the workspace, with an `active` flag for the one the resolved deployment's `spec.tenant` names.                                                                                                                       |
| `history`      | Implemented | The last 20 deploy run/destroy executions from `.strata/logs/`, newest first. No `uri`/`location` — an audit log entry is not a workspace object.                                                                                                                     |
| `promotion`    | Implemented | The last 20 completed promotion records from `.strata/promotions/records/`.                                                                                                                                                                                           |
| `approvals`    | Implemented | Gate results (`spec.gates[]`) recorded on those same promotion records — no separate live re-evaluation.                                                                                                                                                              |
| `variables`    | Implemented | Declared (not resolved) variables from every environment the deployment references — key, store type, and reachability only. Never a value.                                                                                                                           |
| `secrets`      | Implemented | Declared secret keys and their store type only — **never** a value or store pointer, for any store type.                                                                                                                                                              |
| `features`     | Implemented | Declared (not resolved) feature flags, same shape and same rule as `variables`.                                                                                                                                                                                       |
| `values`       | Implemented | The union of `variables` + `secrets` + `features` in one source.                                                                                                                                                                                                      |
| `policies`     | Implemented | Policies declared in `configuration.spec.policies`. The one source needing an active profile's configuration already loaded — `strata diagram show` loads it only when a diagram declares this source, and only when actually rendering (not for `--print-template`). |
| `drift`        | Implemented | Tracked drift entries from `.strata/drift/{deployment}.drift.json` — `drifting`/`resolved`/`acknowledged` status, never a live drift check.                                                                                                                           |
| `repositories` | Implemented | Repositories declared in `.strata/solution.json` — declaration only, never a live `git fetch`/`status`. Any credential embedded in a URL is stripped.                                                                                                                 |
| `sbom`         | Implemented | Components from the cached `sbom.json` (CycloneDX) — images, charts, modules, and app deps. Component identity is not secret-like, so it is shown in full.                                                                                                            |
| `locks`        | Implemented | The deployment's declared `spec.locking` policy — never a live lock-held check against the backend.                                                                                                                                                                   |
| `outputs`      | Implemented | Output keys from the cached `deployment-outputs.json` — key, stage, and sensitivity flag only, **never** the value.                                                                                                                                                   |

`resources`/`modules`/`namespaces` share the same graph `topology` builds — declaring more than
one of them (or `topology` alongside them) in one diagram's `sources` re-parses the workspace
only once, not once per source.

`network`/`firewalls`/`dns` each read the workspace's `spec.networks[]` / `spec.firewalls[]` /
`spec.dns_zones[]` reference lists, which name a file per entry — they too resolve the workspace
document once and share it. A `@repo_name/path` cross-repository reference is reported and
skipped rather than resolved, since resolving one needs a solution-level repository map these
sources do not have.

`stages`/`environments`/`tenants` resolve a deployment the same way — `--entry` if given,
otherwise the first deployment file found — and share that resolution too. `tenants` is the
exception: it has no workspace-level reference list to walk, so it scans every YAML file in the
workspace for `kind: tenant` documents instead, and works even with no deployment at all. When a
deployment does resolve, its `spec.tenant` marks the matching tenant node `active: true`.

`history`/`promotion`/`approvals` read machine-generated state rather than authored YAML:
`history` scans the audit trail in `.strata/logs/` for `deploy_run`/`deploy_destroy` executions;
`promotion` and `approvals` both read `.strata/promotions/records/*.yaml`, the same completed
promotion records `strata promote history` shows — `approvals` just projects each record's
`spec.gates[]` instead of the record itself. Neither triggers a live re-evaluation; both are
capped at the 20 most recent entries so a diagram stays readable. `history` entries have no
`uri`/`location` (an audit log line is not a workspace object, the same reasoning the ADR applies
to drift entries); promotion records do, since they are real `kind: promotion-record` documents —
one of the few places `strata diagram resolve` looks inside `.strata/` on purpose.

`variables`/`secrets`/`features`/`values` never resolve anything — they report what
`spec.variables[]`/`spec.secrets[]`/`spec.features[]` **declares** on every environment the
resolved deployment references, not a live value. `status` is `offline` when the store resolves
with no live contact (`constant`, `environment`, `github`) or `live` otherwise (`vault`,
`bitwarden`, `azure-keyvault`, …) — informational, not a health signal. **None of the four ever
expose a value or a pointer to one, for any kind or any store type — not even `constant`.** A
node's `metadata` is exactly `{store, environment}`. This is deliberately absolute rather than
"safe for constants, withheld for secrets": a `constant` declared today is one accidental edit
away from holding something sensitive, and a diagram — easily screenshotted, exported, or pasted
elsewhere — is not where that mistake should surface. `values` is the union of the other three in
one source. None of these call `ValueController.resolve_values()`, which always attempts live
store contact and returns the actual resolved secret value — a diagram source has no business
doing either.

`policies` is the one source that needs an active profile's configuration already loaded
(everything above loads from a bare file path). `strata diagram show` loads one automatically,
the same way `strata policy list` does, but only when a diagram actually declares a `policies`
source — and not at all for `--print-template`, which only needs the template text. Without an
active profile, it fails with a clear message rather than a silent empty list, since "zero
policies declared" and "no configuration loaded" must not look the same.

`drift`/`outputs`/`locks`/`repositories`/`sbom` read cached or declared state rather than running
anything live. `drift` reads `.strata/drift/{deployment}.drift.json` — an address is `drifting`
when it appeared in the most recent recorded run, `acknowledged` when suppressed on purpose, or
`resolved` otherwise; entries have no `uri`/`location`, the same reasoning `history` uses. `locks`
reports only the deployment's declared `spec.locking` block (`enabled`, `strategy`,
`wait_timeout`, `force_unlock_after`) — never a live "is a lock currently held" check against the
backend, which needs different network/auth per backend type. `repositories` reads
`.strata/solution.json`'s repository list — declaration only, `status` reflects whether the local
path exists on disk (never a `git fetch`/`status` call), and any credential embedded in a
repository URL's userinfo (`https://user:token@host/...`) is stripped before it is surfaced.

`outputs` and `sbom` both read an artifact cached in the deployment's own build directory
(`build/{name}-{version}/`, mirroring `DeploymentService.get_build_path()`) — never triggering a
live `terraform output` or a fresh SBOM scan. `outputs` reads `deployment-outputs.json` and
surfaces only an output's *key*, its stage, and whether it is flagged sensitive — **never the
value**, regardless of what the cache itself claims to have already filtered, since not every
code path that can write that cache reliably applies the same sensitive-value filtering. `sbom`
reads the cached CycloneDX `sbom.json` and surfaces each component's name, version, purl, and
properties — package identity is not secret-like, so it is the one of these five sources shown in
full.

## spec.layout Fields

Used to generate a template when `spec.template` is omitted.

| Field       | Type   | Required | Description                                              |
| ----------- | ------ | -------- | -------------------------------------------------------- |
| `type`      | string | No       | Mermaid diagram type. Default `flowchart`.               |
| `direction` | string | No       | `TD`, `LR`, `BT`, or `RL`. Node/edge diagram types only. |

Supported `type` values: `flowchart`, `sequence`, `gantt`, `pie`, `mindmap`, `class`,
`stateDiagram`, `timeline`, `quadrant`, `sankey`.

> **Only `flowchart` and `stateDiagram` can be generated from `layout`/`style`.** The shorthand
> covers the node/edge case, which is also the only case Mermaid `classDef` styling applies to.
> Every other type is a `spec.template` — which is the primary authoring path anyway, not a
> fallback. Asking for one here fails with a message saying so.

A generated diagram needs at least one entry in `spec.sources` to draw. For a static diagram,
write `spec.template` directly.

## spec.style Fields

Used to generate a template when `spec.template` is omitted.

| Field       | Type   | Required | Description                                                        |
| ----------- | ------ | -------- | ------------------------------------------------------------------ |
| `color_by`  | string | No       | Node field driving colour (e.g. `status`), mapped to a token ramp. |
| `group_by`  | string | No       | Node field to group into subgraphs (e.g. `namespace`).             |
| `highlight` | array  | No       | Conditional emphasis rules.                                        |

`color_by` emits one `classDef` per distinct value **present in the data**, resolved through the
`token` filter. The distinct values are not knowable when the template is generated, so the
generated template derives them at render time rather than guessing a fixed set. A value with no
matching token falls back to `neutral`.

`group_by` wraps nodes in one subgraph per distinct value. A node missing that field lands in an
`(ungrouped)` bucket rather than failing the render.

### highlight Fields

| Field       | Type   | Required | Description                                 |
| ----------- | ------ | -------- | ------------------------------------------- |
| `condition` | string | Yes      | Per-node condition — see the grammar below. |
| `token`     | string | Yes      | Design System token name, e.g. `critical`.  |

Matching nodes get a trailing Mermaid `class` statement that overrides their `color_by` colour
and adds `stroke-width:3px`, so a highlight still reads as emphasis when the token colour is
close to the node's own class.

#### Condition grammar

`condition` is a deliberately small grammar, not raw Jinja:

```
<field> == <value>
<field> != <value>
<field> in [<value>, <value>, ...]
```

- `<field>` is a dotted attribute path on the node — `status`, `kind`, `metadata.role`
- `<value>` is a bare or quoted string; quoting is optional
- Values are always emitted as quoted literals, so an authored value can never become part of
  the expression itself

```yaml
highlight:
  - condition: "status == disabled"
    token: warn
  - condition: "severity in [critical, high]"
    token: critical
  - condition: "metadata.role != web"
    token: neutral
```

A closed grammar means a typo produces a validation error naming the problem, instead of an
expression that silently evaluates to false and leaves you wondering why nothing is highlighted.

**Tokens, never raw colours.** `token` names a Design System entry rather than a hex value, so a
committed diagram stays readable for users on a light theme, a dark theme, or a high-contrast
theme. Unlike the data-derived names `color_by` produces, an authored `token` is validated
strictly — a value that turns up in your data can reasonably be one nobody anticipated, but a
name typed into the YAML is a typo if it does not exist.

### Outgrowing the shorthand

`spec.template` always wins when both are present. To move from the shorthand to a template,
print what the shorthand generates and edit it:

```bash
strata diagram show -f my-diagram --print-template
```

The sugar is never a dead end.

## Examples

### Minimal — layout only, no template

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: topology
  annotations:
    description: "Deployment topology, left to right"
spec:
  sources:
    - type: topology
  layout:
    type: flowchart
    direction: LR
  style:
    color_by: status
    group_by: namespace
```

### Explicit template — a Mermaid type with no layout sugar

`layout`/`style` only cover the node/edge case. Anything else is a template — and templates are
not a fallback, they are the primary path:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: drift-by-severity
  annotations:
    description: "Share of drifted resources by severity"
spec:
  sources:
    - type: drift
  template: |
    pie title Drift by severity
    {% for severity, count in drift.by_severity.items() %}
      "{{ severity }}" : {{ count }}
    {% endfor %}
```

### Filtered sources bound to distinct names

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: prd-vs-acc
spec:
  sources:
    - type: resources
      as: prd
      filter:
        environment: prd
    - type: resources
      as: acc
      filter:
        environment: acc
  template: |
    flowchart LR
      subgraph acc
      {% for r in acc.items %}
        acc_{{ r.id }}["{{ r.name }}"]
      {% endfor %}
      end
      subgraph prd
      {% for r in prd.items %}
        prd_{{ r.id }}["{{ r.name }}"]
      {% endfor %}
      end
```

## Template Helpers

Strata registers a small set of Jinja filters — deliberately few, since Jinja's built-ins cover
most needs. These three exist because they depend on strata's own conventions and a template
cannot derive them for itself.

| Filter              | Purpose                                                                    |
| ------------------- | -------------------------------------------------------------------------- |
| `\| slug`           | Make any string a Mermaid-safe node ID (`@repo/a-b.yaml` → `at_repo_a_b`). |
| `\| token`          | Resolve a Design System token name to a Mermaid `classDef` body.           |
| `\| mermaid_escape` | Escape quotes and newlines for safe use inside a node label.               |

Jinja's built-in filters (`upper`, `trim`, `groupby`, `selectattr`, …) all work as normal. A
filter the environment does not provide fails validation rather than render time.

### `token`

```jinja
classDef critical {{ 'critical' | token }}      → classDef critical fill:#f8d7da,stroke:#dc3545
{{ 'high' | token('fill') }}                    → #ffe5d0
{{ 'high' | token('stroke') }}                  → #fd7e14
```

Unknown token names fall back to `neutral` rather than failing — a diagram colouring by a field
whose values were not anticipated should still render.

Severity ramps share one 5-step scale, so "red = critical" means the same thing in every diagram:

| Domain               | Tokens                                                          |
| -------------------- | --------------------------------------------------------------- |
| Validity status      | `valid` `invalid` `missing` `external` `orphan`                 |
| Severity (drift/CVE) | `info` `unknown` `low` `medium` `high` `critical`               |
| Policy enforcement   | `audit` `warn` `deny`                                           |
| Lock/promotion state | `unlocked` `locked` `held` `expired`                            |
| Health check result  | `unknown` `passing` `degraded` `failing`                        |
| Deployment outcome   | `success` `partial` `failed`                                    |
| Taxonomy/kind        | `resource` `module` `namespace` `network` `disabled` `dangling` |
| Fallback             | `neutral`                                                       |

CLI output carries hex, because it targets Mermaid Live and GitHub-rendered markdown, which have
no editor theme to read from. The VS Code webview maps the same token names onto
`--vscode-charts-*` CSS variables instead, which is what keeps a committed diagram readable on a
light, dark, or high-contrast theme.

## Validation Rules

`strata validate` checks a diagram without rendering it:

- `meta.name` must match `^[a-z0-9][a-z0-9_-]*$`
- `sources[].as` must match `^[a-z_][a-z0-9_]*$` (valid Jinja identifier), max 64 characters
- Source context names must be unique within a diagram
- At least one of `spec.template` or `spec.layout` must be present
- `spec.template` must parse as Jinja2 and reference only filters the environment provides
- Every variable `spec.template` references must be bound by `spec.sources`
- Every `spec.style.highlight[].condition` must match the condition grammar
- Every `spec.style.highlight[].token` must be a real design token
- Unknown fields are rejected — models use `extra="forbid"`

The template rules exist because both failures are otherwise silent: an unbound name renders as
nothing, so a typo like `{{ topolgy.nodes }}` produces an empty-but-valid diagram rather than an
error. Loop variables, `{% set %}` assignments, and filters are not treated as unbound — Jinja
resolves those itself.

Layout-type and source-count problems are reported by `strata diagram show` rather than
`strata validate`, because whether a template can be *generated* is a rendering question, not a
schema one.

## Node Identity

Every node a source produces carries a `uri` — a structural `strata://` identifier — and
templates emit it as a Mermaid `click` directive:

```jinja
click {{ n.id }} "{{ n.uri }}"
```

That is what makes the connection back to the workspace survive leaving the editor: paste the
Mermaid into a README and the identity comes along. The URI encodes no line number, so
reformatting or reordering YAML above the target does not break it. Resolve one on demand:

```bash
strata diagram resolve strata://workspace/platform/resource/app_server
# stack/workspace.yaml:42
```

See [`diagram resolve`](../platform/commands.md#diagram-resolve) for the URI grammar.

## Related

- [ADR-0034 — Diagram Visualization](../decisions/0034-diagram-visualization-in-vscode-extension.md)
- [ADR-0017 — Jinja2 Template Engine](../decisions/0017-jinja2-template-engine.md)
- [Deployment Configuration](deployment.md)
- [Workspace Configuration](workspace.md)
