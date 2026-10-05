# Output Profile Model (`emits` gating) — Design

- Status: draft
- Last updated: 2026-10-05

## Overview

`terraform_projection.py`'s `default_output()` unconditionally writes every
non-empty category (13 today: `workspace`/`providers`/`resx_<type>`/
`topologies`/`namespaces`/`flags`/`variables`/`properties`/`custom`/
`tenant`/`dns`/`networks`/`firewalls`) as its own `*.auto.tfvars.json` file.
Terraform tolerates an undeclared `.tfvars` key as a warning, not an error,
so this is safe against a `.tf` root written *for* strata — but a
provisioner pointed at **pre-existing, foreign** Terraform code (the
"layer strata onto code that already exists" case ADR-0023 D2 explicitly
promises to support) gets an undeclared-variable warning on every single
`plan`/`apply`, once per category it never declared.

v1 solved this with an opt-in allowlist, `OutputProfileModel` (`output:
{format, emits, files}` on a provisioner) — port its `format: custom` +
`emits: [...]` combination, the only part with confirmed real usage.

**Real, confirmed need** (not hypothetical): `cfg-deployment`'s
`control/workspace.yaml` has a real, load-bearing

```yaml
output:
  format: custom
  emits: [features, variables, properties]
```

which hit and got a fix for a real strata v1 bug (PR #309, "track written
files by provisioner path"). ADR-0023 D2 originally decided *not* to port
`OutputProfileModel` at all, based on checking the other two real workspaces
available and finding zero usage — that conclusion didn't survive checking
the third.

**Why this wasn't built already, until now:** `emits` has nothing to gate
until the categories it gates exist. `features`/`variables`/`properties`/
`custom` didn't exist in v2's `default_output()` until
[build-time-value-categories.md](build-time-value-categories.md) (status:
implemented) built them. That prerequisite is now done — this doc is the
next, previously-blocked step, not a re-litigation of D2.

**`emits` is hand-authored, never introspected from the real `.tf` root —
a deliberate, already-decided separation, not an oversight.** The operator
writes `emits: [...]` themselves, based on their own knowledge of what their
`.tf` code declares; strata never parses `control`'s actual `variable {}`
blocks to compute it. This is a real question worth stating explicitly
because the codebase *does* already have real Terraform HCL-parsing
infrastructure (`python-hcl2`, used today by `terraform_collector.py` for
SBOM generation), and
[ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
defines a parallel concept, **Interface** ("what can this provisioner/root
accept?", derived by literally globbing `*.tf` and parsing `variable {}`
blocks via HCL — v1's real `parse_variables_tf()`), that does exactly what
one might expect `emits` to also do. ADR-0002 settles that these stay two
separate mechanisms, not one to unify later:

> Injection governs the Environmental channel only. The Structural channel
> (the platform's own composed model — resources, topologies, modules) is a
> separate, already-partially-modeled mechanism in v1
> (`OutputProfileModel.emits[]`) and stays separate in v2 — it must not grow
> into the same concept as Injection.

Interface/Injection governs the *Environmental* channel (`variables`/
`features`/`secrets` sourced from `Configuration`/environment stores) and is
**not yet built** in v2 at all (`docs/work/provisioning-injection-model.md`
tracks it, zero implementation today). `emits` governs the *Structural*
channel (this doc's 13 `terraform_projection.py` categories) and is
declared, matching v1's `OutputProfileModel`'s own real behaviour exactly —
not a gap to eventually close with introspection, a different concept by
design.

## Current Design

`ProvisionerModel.output: OutputModel | None` already exists
(`provisioning_model.py`) for an unrelated mechanism — ADR-0023 D3's Jinja2
full-file-template escape hatch:

```python
class OutputModel(PlatformBaseModel):
    template: str | None = Field(None, ...)
```

**v1's real field is spelled the same way** — `output: {format, emits,
files}` sits directly on a provisioner, exactly where v2's `output.template`
already sits. This is a real naming collision to resolve, not two unrelated
features that happen to share a YAML key by coincidence: a human authoring
one provisioner has exactly one `output:` block to reach for, whichever
mechanism they need.

`default_output()` (`terraform.py`) currently takes no profile at all —
`build_platform_projection()` always returns every non-empty category,
`planned_files()` writes one file per key, unconditionally.

## Proposed Design

**Extend `OutputModel`, don't add a sibling field.** One `output:` block per
provisioner, two mutually exclusive modes:

**Confirmed naming mismatch with v1 — `emits` values follow v2's own
category names, not v1's.** `build-time-value-categories.md`'s own
Changelog records a deliberate v2 naming choice: the payload/filename key is
`"flags"` (`flags.auto.tfvars.json`), never `"features"` — checked and
applied specifically because `planned_files()` derives the filename
directly from the dict key. v1's real `OutputProfileModel.emits` vocabulary
uses `features` (confirmed: `cfg-deployment`'s real example is literally
`emits: [features, variables, properties]`). These two facts collide:
`EmitCategory` below uses v2's own `flags`, consistent with every other
category name already being v2's own (not a v1 alias layer) — which means
`cfg-deployment`'s real YAML would need `features` rewritten to `flags`
when it actually migrates to v2. A deliberate breaking rename, not an
oversight — flagged here so it isn't missed at migration time, and so
nobody silently adds a `features = "features"` alias later assuming it's
an omission.

```python
class EmitCategory(str, Enum):
    """Matches `build_platform_projection()`'s own payload keys exactly —
    v2's real, on-disk category names (see naming note above), not v1's
    vocabulary. `resources` (not `resx_<type>`) - `emits` gates by the
    user-facing concept (v1's own `EmitCategory.RESOURCES`), independent of
    how many distinct resource types happen to be in play; `planned_files()`
    already treats every `resx_<type>` as the same `TF_VAR_resources`
    variable at deploy time (`merged_resources`), so gating follows the
    same grain.
    """
    WORKSPACE = "workspace"
    PROVIDERS = "providers"
    RESOURCES = "resources"
    TOPOLOGIES = "topologies"
    NAMESPACES = "namespaces"
    FIREWALLS = "firewalls"
    DNS = "dns"
    NETWORKS = "networks"
    TENANT = "tenant"
    FLAGS = "flags"
    VARIABLES = "variables"
    PROPERTIES = "properties"
    CUSTOM = "custom"


class OutputModel(PlatformBaseModel):
    template: str | None = Field(None, ...)  # unchanged (D3)
    format: Literal["custom"] | None = Field(
        None,
        description="Emit-suppression mode. Only 'custom' has confirmed real usage - "
        "v1's 'strata'/'script'/'none' are not ported, see Remaining Work.",
    )
    emits: list[EmitCategory] | None = Field(
        None,
        description="Allowlist of categories default_output() actually writes. Unset emits everything "
        "(today's behaviour, unchanged) - only meaningful together with format: custom.",
    )

    @model_validator(mode="after")
    def validate_template_xor_emits(self) -> "OutputModel":
        """`template` replaces default_output() entirely (D3) - nothing left for
        `emits` to gate once it's set. Reject both set together rather than
        silently ignoring one."""
        if self.template is not None and (self.format is not None or self.emits is not None):
            raise ValueError("'output.template' and 'output.format'/'output.emits' are mutually exclusive.")
        if self.emits is not None and self.format is None:
            raise ValueError("'output.emits' requires 'output.format' to be set.")
        return self
```

**Verified against real Terraform (1.12.2) — the warning comes from the
on-disk tfvars file ONLY; `TF_VAR_` env vars never warn.** An earlier
draft of this doc claimed both input paths warn identically. That was
wrong, and the correction matters enough to state up front. Tested directly
with a root declaring only `variable "customer_code"`:

| How the undeclared `display_name` was supplied | Result                                     |
| ---------------------------------------------- | ------------------------------------------ |
| `terraform.auto.tfvars.json`                   | **Warning: Value for undeclared variable** |
| `TF_VAR_display_name` env var                  | **No warning at all**                      |

Terraform's own warning text says so explicitly:

```
Warning: Value for undeclared variable

The root module does not declare a variable named "display_name" but a value
was found in file "terraform.auto.tfvars.json". If you meant to use this
value, add a "variable" block to the configuration.

To silence these warnings, use TF_VAR_... environment variables to provide
these values.
```

Terraform deliberately does not warn for env vars — the ambient environment
routinely carries unrelated `TF_VAR_*` values, so warning on them would be
noise. **This means `emits`'s whole reason to exist applies to wiring point
1 only.**

**Wiring point 1 (build time) — the only path that actually warns.**
`InfraIntegration.prepare()`'s existing `else` branch:

```python
for filename, content in self.default_output(resolved, provisioner, graph).items():
    if not is_emitted(category_for_filename(filename), provisioner.output):
        continue
    (path / filename).write_text(content)
```

**One shared gate, defined once, called from four places — not four
independent inline checks.** Repeating `provisioner.output and
provisioner.output.emits and X not in provisioner.output.emits` at every
call site risks the two wiring points drifting apart (e.g. one site
treating an unset `emits` differently from another). Defined in
`terraform_projection.py`, next to `EmitCategory`/`FLAT_CATEGORIES`/
`real_variable_name()` — the module that already owns the category
vocabulary, not `capabilities.py` or `deploy_controller.py`:

```python
# terraform_projection.py

def is_emitted(category: EmitCategory, output: OutputModel | None) -> bool:
    """Whether `category` should actually be written/delivered, given a
    provisioner's `output.emits` allowlist. Unset `output` or unset `emits`
    emits everything - today's unchanged default. The one gate both wiring
    points call, so `emits` can never drift between a build-time file write
    and a deploy-time env var delivery.
    """
    if output is None or output.emits is None:
        return True
    return category in output.emits


def category_for_filename(filename: str) -> EmitCategory:
    """Map a `planned_files()`-produced filename back to its `EmitCategory`
    (wiring point 1's open question 1, resolved). `resx_<type>.auto.tfvars.json`
    -> `RESOURCES` (collapsing every resource type to one gate, matching
    `merged_resources`'s existing deploy-time precedent) - every other
    filename is `<category>.auto.tfvars.json`, its stem equal to the
    category 1:1, so no string-matching table to maintain by hand.
    """
    stem = filename.removesuffix(".auto.tfvars.json")
    return EmitCategory.RESOURCES if stem.startswith("resx_") else EmitCategory(stem)
```

**Wiring point 1 (build time)** — `InfraIntegration.prepare()`'s existing
`else` branch:

```python
for filename, content in self.default_output(resolved, provisioner, graph).items():
    if not is_emitted(category_for_filename(filename), provisioner.output):
        continue
    (path / filename).write_text(content)
```

**Wiring point 2 (deploy time) — real, but OPTIONAL, and not a
warning-avoidance measure.** `deploy_controller.py`'s `deploy_run()` has a
second, entirely separate, currently unconditional delivery path that
injects every category as a `TF_VAR_x` env var. Per the verified table
above, **none of these produce a warning**, so gating them does nothing for
this design's stated goal. Gating them anyway is defensible on other
grounds — not handing a foreign root data its author never asked for,
keeping build-time and deploy-time behaviour consistent so `emits` means
one thing rather than two — but those are hygiene arguments, not the
warning argument, and they should be weighed on their own merits rather
than assumed. Were it gated, the four call sites are:

```python
for category, docs in dns_networks_firewalls.items():
    if not is_emitted(EmitCategory(category), provisioner.output):
        continue
    ...

for name, payload in configuration_payloads.items():
    if name in FLAT_CATEGORIES or name.startswith("resx_"):
        continue
    if not is_emitted(EmitCategory(name), provisioner.output):
        continue
    ...

for name in FLAT_CATEGORIES:
    if not is_emitted(EmitCategory(name), provisioner.output):
        continue
    ...

if merged_resources and is_emitted(EmitCategory.RESOURCES, provisioner.output):
    resolved_resources = resolve_value_tokens_in_mapping(merged_resources, tokens)
    env[f"{integration.ENV_VAR_PREFIX}resources"] = json.dumps(resolved_resources)
```

**The bigger consequence — see "Alternative: env-var-only delivery" below.**
If env vars never warn, and `deploy run` already delivers all 13 categories
via env vars with fully-resolved values, then simply *not writing* the
tfvars files takes warnings to exactly zero for every category and every
key — no allowlist, no drift, no key-granularity gap. That is a materially
stronger answer to this doc's own problem statement than `emits` is.

## Alternative: env-var-only delivery (competes with `emits`, may replace it)

**Problem with `emits` as a warning-avoidance mechanism, stated plainly:**
it does not reach zero warnings. It reaches *fewer* warnings, and only
until the next variable is added (worked example 6a). A mechanism that
leaves a residual, routinely-reappearing warning is arguably worse than one
that doesn't try: a `plan` output that is clean *except for two warnings
everyone knows to ignore* trains an entire team to stop reading warnings,
which is how the genuinely important ones get missed. "Mostly silent" is
not a stable equilibrium — it degrades into "nobody reads `plan` output".

**The alternative, from the verified finding above:** Terraform warns on
auto-loaded `*.auto.tfvars.json` and does not warn on `TF_VAR_*`. strata
already delivers **all 13 categories** via `TF_VAR_*` at deploy time
(`deploy_controller.py`, gap #8/#12/#17), with *fully-resolved* values —
strictly more correct than the on-disk files, which deliberately carry
unresolved `${var:}`/`${secret:}` tokens for dns/networks. So the files are
not load-bearing for the deploy path at all; they are duplicated,
less-resolved copies of data the env vars already carry.

**This repo already made exactly this call once, for exactly this reason.**
`write_resolved_manifest()`'s own docstring (`build_controller.py`):

> Write `build_path/resolved.yaml` — plain YAML, deliberately **not**
> `*.auto.tfvars.json`: Terraform never auto-loads it, so no `.tf` module
> needs a matching `variable {}` block just to accommodate strata's own
> bookkeeping output.

That is the identical reasoning, already accepted, already shipped — just
applied to one file instead of all thirteen.

**Three shapes this could take**, in increasing order of disruption:

1. **Rename, don't delete — verified against real Terraform 1.12.2, not
   just asserted.** Keep writing every category, but as
   `<category>.tfvars.json` (no `.auto.`), which Terraform's auto-load
   convention only recognizes as `terraform.tfvars(.json)` or
   `*.auto.tfvars(.json)` — anything else sitting in the directory is
   **completely inert**, not read at all, unless something explicitly
   passes `-var-file=<name>`. Tested directly: a `variables.tfvars.json`
   (non-auto name) containing both `customer_code` (declared) and
   `display_name` (not) sitting next to `main.tf`, with a plain
   `terraform plan` — Terraform never opens the file at all; `customer_code`
   comes back as a **missing required variable error**, because the file
   genuinely was never read, not silently accepted. Only once `-var-file
   variables.tfvars.json` is passed explicitly does the file get read —
   and *then* the same "Warning: Value for undeclared variable" for
   `display_name` reappears, exactly as it would for an auto-loaded file.
   **So the rename doesn't make the warning impossible — it makes it
   opt-in.** `deploy run` never passes `-var-file` (it already delivers
   everything via `TF_VAR_*`, unaffected by this rename), so the automated
   path stays silent; a human deliberately running `terraform plan
   -var-file=...` by hand against the build dir gets the warning back,
   which is correct — at that point they chose to load data the root
   doesn't declare, same as passing `-var` for anything else undeclared.
   The build artifact stays fully inspectable (the real reason the files
   exist — CI artifact upload, human debugging, `strata build run`
   producing something you can look at), zero warnings on every path
   `deploy run` actually exercises, no schema field, no allowlist, and
   nothing to drift. Deploy-time `TF_VAR_*` delivery is unchanged and
   already does the real work.
2. **A provisioner-level switch** (e.g. `output.format: env`) choosing
   between auto-loaded files and env-only, for the operator who *wants*
   `terraform plan` to work standalone in the build dir.
3. **`emits` as currently designed** — hand-authored category allowlist.
   Highest ongoing cost (an operator must maintain it, and it silently
   stops being correct when a variable is added), lowest ceiling (never
   reaches zero warnings).

**Worked example — option 1 end to end, both paths, verified directly.**
`control_iac` provisioner, `build/<deployment>/control/variables.tfvars.json`
contains the real resolved `{"customer_code": "c0062", "display_name":
"Acme Corp"}` (every key, no allowlist needed — `display_name` can be added
freely, unlike the `emits` case in example 6a):

| Path                                                                    | What runs                                                                                                                                                      | Result                                                                                                                                                                         |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Automated** (`deploy run`)                                            | `terraform plan`/`apply` with `TF_VAR_customer_code`/`TF_VAR_display_name` already set as env vars (gap #8/#17's existing delivery, unaffected by this rename) | Zero warnings — `variables.tfvars.json` sits on disk, unread, inert                                                                                                            |
| **Manual, no setup**                                                    | A human `cd`s into `build/<deployment>/control` and runs plain `terraform plan`                                                                                | `customer_code` comes back **missing** (confirmed: the file is never read) — not silent, an explicit error telling them a value they expected isn't there                      |
| **Manual, one-time setup**                                              | `$env:TF_CLI_ARGS_plan = "-var-file=variables.tfvars.json"` set once (shell profile/CI job/`.envrc`), then plain `terraform plan`                              | Verified directly: `customer_code` resolves correctly, **zero flags typed, zero `.tf` edits** — `TF_CLI_ARGS_plan` makes Terraform behave as if `-var-file` were always passed |
| **Manual, one-time setup, but `display_name` wasn't declared anywhere** | Same `TF_CLI_ARGS_plan` setup as above                                                                                                                         | "Warning: Value for undeclared variable" reappears for `display_name` — correct: the human explicitly opted into reading the whole file, same as passing `-var-file` by hand   |

The middle two rows are the actual tradeoff this option makes: a manual
user gets **either** silence-by-not-reading-the-file **or**
warnings-when-they-explicitly-opt-in — never the "quietly wrong/incomplete"
middle ground `emits` risks (example 6a), and never a surprise, since the
missing-variable error in row 2 makes the opt-in requirement obvious rather
than hiding it.

**Verified against both real consumers' actual CI — nothing depends on the
auto-load convention.** Read `haven`'s real `.github/workflows/deploy-infra.yml`
and `cfg-int-deployment`'s real `.github/workflows/deploy.yml` directly
(not assumed). Both share the identical shape:

```yaml
- name: Build platform artifacts
  run: strata build run --file $DEPLOYMENT_FILE
- name: Upload build output
  uses: actions/upload-artifact@v7
  with: { name: platform-build, path: build/ }   # whole directory, no filename pattern
# ...download-artifact in a later job, same shape...
- name: Deploy infrastructure
  run: strata deploy run --file $DEPLOYMENT_FILE --force --scope infra
  # haven's own comment: "the terraform deployer auto-injects each resolved
  # secret as TF_VAR_<key> before plan/apply/destroy — no manual TF_VAR_*
  # wiring needed here"
```

Three things this confirms, each closing a different risk: (1)
`upload-artifact`/`download-artifact` operate on the whole `build/`
directory with no filename filtering — the rename is invisible to this
step regardless of extension; (2) `deploy run` already injects `TF_VAR_*`
as a direct Python subprocess env dict, confirmed by the pipeline author's
own comment that this needs no pipeline-side wiring at all — nothing for
CI to "set up" for the rename to work; (3) the only raw `terraform`
invocation found in either pipeline is `terraform show` on an
already-produced `.tfplan` binary, later in haven's workflow — it never
reads `.tfvars`/`.auto.tfvars.json` at all. **Neither real consumer's CI
depends on the auto-load convention anywhere** — the rename is safe for
both without any pipeline change.

**Relationship to `emits`:** option 1 solves the warning problem completely
and makes `emits` unnecessary *for that purpose*. `emits` would then only
be justified if there's a separate, real reason to suppress a category
entirely (not yet evidenced) — which matters, because `emits` is a
**schema field**, and a schema field shipped in v2 is effectively permanent.
Adding one that a better mechanism immediately makes redundant is the exact
failure mode ADR-0023 D2 was originally right to worry about.

## Worked Examples

**1. Default — `output` unset.** Today's behaviour, unchanged.

```yaml
provisioners:
  - name: app_iac
    tool: terraform
    source: {repository: myapp, source_path: infra/terraform}
```

`build run` writes all 13 non-empty categories:
`workspace.auto.tfvars.json`, `providers.auto.tfvars.json`,
`resx_compute.auto.tfvars.json`, `topologies.auto.tfvars.json`,
`namespaces.auto.tfvars.json`, `firewalls.auto.tfvars.json`,
`dns.auto.tfvars.json`, `networks.auto.tfvars.json`,
`tenant.auto.tfvars.json`, `flags.auto.tfvars.json`,
`variables.auto.tfvars.json`, `properties.auto.tfvars.json`,
`custom.auto.tfvars.json` (fewer if a category is genuinely empty for this
workspace — e.g. no `tenant` reference).

**2. `cfg-deployment`'s real case, translated to v2 naming.** A provisioner
pointed at foreign `.tf` code that only declares three variables:

```yaml
provisioners:
  - name: control_iac
    tool: terraform
    source: {repository: iac-deployment, source_path: control}
    output:
      format: custom
      emits: [flags, variables, properties]   # v1 wrote "features", see naming note above
```

`build run` writes **only** `flags.auto.tfvars.json`,
`variables.auto.tfvars.json`, `properties.auto.tfvars.json` to disk (wiring
point 1) — and that alone is sufficient to silence the undeclared-variable
warnings for the other ten categories, because the on-disk auto-loaded file
is the only input path that warns (verified above). `deploy run`'s
`TF_VAR_resources`/`TF_VAR_dns_zones`/`TF_VAR_platform_providers` env vars
still being set for the suppressed categories produces **no warning at
all** — gating them (wiring point 2) is a hygiene choice, not a
warning-avoidance requirement.

Note what this example does *not* achieve: zero warnings overall. It
achieves zero warnings *for the ten suppressed categories*. The three
emitted ones still warn for every key the real root doesn't declare — see
example 6a.

**2a. Wiring point 2 in detail — the `dns`/`networks`/`firewalls` loop.**
Same `control_iac` provisioner as example 2, plus a `firewalls` document
the workspace declares but never emits:

```python
for category, docs in dns_networks_firewalls.items():   # {"dns": {...}, "networks": {...}, "firewalls": {...}}
    if not is_emitted(EmitCategory(category), provisioner.output):
        continue   # "firewalls" skipped; "dns"/"networks" also skipped (not in this provisioner's emits either)
    ...
    env[f"{integration.ENV_VAR_PREFIX}{real_variable_name(category)}"] = json.dumps(resolved_payload)
```

Result: `env` ends up with no `TF_VAR_dns_zones`/`TF_VAR_networks`/
`TF_VAR_firewalls` key at all for this step — not an empty-but-present
value, genuinely absent, matching "the file was never written" at build
time exactly.

**2b. Wiring point 2 in detail — the ten-category broadcast loop.** Same
provisioner; `configuration_payloads` has all ten keys computed (`graph`
already has the data), but only three are allowed out:

```python
for name, payload in configuration_payloads.items():   # workspace, providers, resx_compute, topologies, namespaces, flags, variables, properties, custom, tenant
    if name in FLAT_CATEGORIES or name.startswith("resx_"):
        continue   # handled by their own loops/call site below, unchanged
    if not is_emitted(EmitCategory(name), provisioner.output):
        continue   # "providers"/"topologies"/"namespaces"/"tenant" all skipped here
    env[f"{integration.ENV_VAR_PREFIX}{real_variable_name(name)}"] = json.dumps(resolved_payload)
```

Only `tenant` (if non-empty) would have reached this loop anyway as a
non-`FLAT_CATEGORIES`, non-`resx_` entry — gated out the same way.

**2c. Wiring point 2 in detail — `FLAT_CATEGORIES`'s per-key loop, gated
*before* the per-key `for`, not per-key.** `emits: [flags, variables,
properties]` excludes `workspace`/`custom` (both `FLAT_CATEGORIES`
members):

```python
for name in FLAT_CATEGORIES:   # workspace, flags, variables, properties, custom
    if not is_emitted(EmitCategory(name), provisioner.output):
        continue   # checked once per *category*: "workspace"/"custom" skipped entirely
    payload = configuration_payloads.get(name, {})
    if not payload:
        continue
    resolved_payload = resolve_value_tokens_in_mapping(payload, tokens)
    for key, value in resolved_payload.items():   # only now does it iterate workspace_name, environment, etc.
        env[f"{integration.ENV_VAR_PREFIX}{key}"] = json.dumps(value)
```

Gating here at the category level (before the inner `for key` loop) rather
than per-key is deliberate, not incidental: `workspace`'s six keys
(`workspace_name`/`workspace_version`/`deployment_name`/`environment`/
`platform_version`/`labels`) have no individual `EmitCategory` of their own
— `emits` was designed to suppress whole categories (matching v1's own
`EmitCategory.WORKSPACE` granularity), not to let someone emit
`workspace_name` while suppressing `environment`.

**2d. Wiring point 2 in detail — the `merged_resources` call site (the
fourth, previously missed even by this doc's own first pass).** `emits:
[resources]` (example 3 below) gates it; the `cfg-deployment`-translated
example (2/2a-c) has no `resources` in its `emits` list, so for that
provisioner specifically:

```python
if merged_resources and is_emitted(EmitCategory.RESOURCES, provisioner.output):
    ...
    env[f"{integration.ENV_VAR_PREFIX}resources"] = json.dumps(resolved_resources)
```

`TF_VAR_resources` is never set at all for `control_iac` — `merged_resources`
being non-empty (the workspace really does have resources attached) is not
enough on its own; `is_emitted()` must also pass.

**3. `emits: [resources]` collapsing a multi-type split (open question 3).**

```yaml
output:
  format: custom
  emits: [resources]
```

With both `compute` and `network`-typed resources attached to the
workspace, this writes **both** `resx_compute.auto.tfvars.json` and
`resx_network.auto.tfvars.json` — `resources` is one gate for the whole
category regardless of how many `resx_<type>` files it happens to split
into, matching `merged_resources`'s existing deploy-time precedent.

**4. Rejected — `template` and `emits` together.**

```yaml
output:
  template: infra/terraform/strata.tfvars.json.j2
  emits: [flags]
```

Raises at document-load time (`strata validate`/any command that parses
this provisioner): `"'output.template' and 'output.format'/'output.emits'
are mutually exclusive."` — a template replaces `default_output()` entirely
(D3), so there is nothing left for `emits` to gate.

**5. Rejected — `emits` without `format`.**

```yaml
output:
  emits: [flags]
```

Raises `"'output.emits' requires 'output.format' to be set."` — `format` is
required precisely because v1's `strata`/`script`/`none` values are **not**
ported (open question 2); an `emits`-without-`format` document can't be
silently assumed to mean `format: custom`, since that assumption is exactly
the kind of unevidenced guess this design is trying to avoid elsewhere.

**6. Full end-to-end, with a real declared variable (`customer_code`) —
ties `emits` together with Phase 3's already-built backend token
substitution.** `customer_code` isn't anything new: it's declared exactly
the way any variable is today, in an `environment` document's
`spec.variables` (`store: constant`, same shape this repo's own
`test_deploy_run_resolves_backend_configuration_tokens` fixture uses for
`tf_state_resource_group`):

```yaml
# environments/prd.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: customer_code
      store: constant
      value: c0062
```

The workspace's provisioner references it two different ways — `backend.
configuration`'s `${var:customer_code}` token (Phase 3, already built,
deploy-time only) and `output.emits` (this doc, gates whether
`customer_code` reaches Terraform as part of `variables.auto.tfvars.json`
at all):

```yaml
# workspace.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [p1]
  provisioners:
    - name: control_iac
      tool: terraform
      source: {source_path: infra}
      backend:
        type: azurerm
        configuration:
          resource_group_name: ${var:tf_state_resource_group}
          key: int-${var:customer_code}-${var:environment}.tfstate
      output:
        format: custom
        emits: [flags, variables, properties]
  execution:
    - name: apply_infra
      provisioner: control_iac
      targets: [r1]
  resources:
    - name: r1
      resource: r1
```

Two genuinely different things happen to `customer_code`, at two different
times, through two different mechanisms — worth tracing both all the way
through:

- **Build time (`build run`):** `emits` includes `variables`, so
  `variables.auto.tfvars.json` is written containing `{"customer_code":
  "c0062"}` (wiring point 1). `backend.configuration`'s `${var:customer_code}`
  token is **not** touched here at all — build never resolves backend
  tokens (that's Phase 3's whole point, ADR-0022 D4).
- **Deploy time (`deploy run`):** two independent things happen with the
  *same* resolved value, neither aware of the other:
  1. `resolve_value_tokens_in_mapping(provisioner.backend.configuration,
     tokens)` resolves `key: int-${var:customer_code}-${var:environment}.tfstate`
     to `key: int-c0062-prd.tfstate`, passed to `terraform init
     -backend-config=key=int-c0062-prd.tfstate` — this always happens,
     `emits` plays no part in it (`backend.configuration` is a different
     field, gated by nothing in this design).
  2. `is_emitted(EmitCategory.VARIABLES, provisioner.output)` is checked
     separately (wiring point 2) — `variables` is in `emits`, so
     `TF_VAR_variables` (actually delivered per-key, `FLAT_CATEGORIES`) is
     also set, redundantly re-stating `customer_code=c0062` the env already
     has written to disk since build time.

The two mechanisms can disagree, and that's fine, not a bug: setting
`emits: [flags, properties]` (dropping `variables`) would stop
`variables.auto.tfvars.json`/`TF_VAR_variables` delivery entirely, while
`backend.configuration`'s own `${var:customer_code}` token keeps resolving
regardless — the backend config is a different Terraform input mechanism
entirely (`-backend-config`, consumed by `terraform init`, not a declared
root `variable {}` at all), so it was never going to warn about an
undeclared variable in the first place, and `emits` was never designed to
reach it.

**6a. Confirmed real limitation — adding `display_name` alongside
`customer_code` reintroduces exactly the warning this design exists to
avoid, and `emits` cannot stop it.** Continuing example 6's real `.tf` root
(declares only `variable "customer_code" {}`, nothing else): add a second
constant variable to the same `environment.yaml`:

```yaml
spec:
  variables:
    - key: customer_code
      store: constant
      value: c0062
    - key: display_name
      store: constant
      value: Acme Corp
```

`emits` already includes `variables` (needed to deliver `customer_code` at
all) — `_build_variables_payload()` is `{ref.key: ref.value for ref in
graph.variable_refs if ref.value is not None}`, every declared variable
with a resolved value, **no filtering by category membership below the
category itself**. `display_name` rides along automatically into the same
`variables.auto.tfvars.json`. The real `.tf` root has no `variable
"display_name" {}` block, and that file *is* auto-loaded, so `terraform
plan`/`apply` now warns — the exact failure mode this whole design exists
to prevent, reintroduced by a config change that never touched `emits` at
all. (The matching `TF_VAR_display_name` env var is harmless and silent;
the auto-loaded file is the entire problem.)

**This is not a bug in the proposed design — it is `emits`'s real,
confirmed granularity limit, worth stating plainly rather than discovering
later.** `emits` gates whole categories (13 of them); it has no concept of
"this individual key within `variables` has a matching declaration and that
one doesn't." The only lever this design gives an operator once `variables`
is in `emits` is **all-or-nothing for the whole category** — remove
`variables` from `emits` entirely (losing `customer_code` too) or accept
`display_name`'s warning. There is no partial answer within this doc's
scope.

**The actual fix is the different, not-yet-built mechanism already flagged
above — ADR-0002's Interface/Injection, not an extension of `emits`.**
`Injection = Interface ∩ Environment` would filter at exactly this
granularity: `Interface` (real HCL parse of the `.tf` root) would contain
`{customer_code}` and not `{display_name}`, so the intersection naturally
excludes `display_name` without anyone hand-maintaining an allowlist of
*individual variable keys* — which `emits` was never designed to be (it
allowlists categories, not keys, matching v1's own real granularity
exactly). Do not solve this by adding key-level filtering to `emits`/
`EmitCategory` — that would blur the Structural/Environmental split
ADR-0002 already deliberately drew; it belongs in Interface/Injection's own
future design instead.

## Related Decisions

- [ADR-0023](../decisions/0023-build-output-rendering.md) — D2 originally
  decided not to port `OutputProfileModel` ("zero usage"), later corrected
  once `cfg-deployment` was checked; D3's `OutputModel`/`template` is the
  field this design extends rather than duplicates.
- [build-time-value-categories.md](build-time-value-categories.md) — built
  the `flags`/`variables`/`properties`/`custom` categories this design's
  `emits` now has something real to gate.
- [build-command.md](build-command.md) — tracks the original gap and the
  2026-09-25 investigation that found/cleared the prerequisite.
- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
  — defines the parallel Interface/Injection concept (Environmental channel,
  HCL-introspection-derived, not yet built) and explicitly decides it stays
  separate from `emits` (Structural channel, hand-authored) forever, not a
  future-unification target.

## Remaining Work / Open Questions

1. **`category_for_filename(filename)` shape — resolved with a concrete
   proposal above (Proposed Design), not yet implemented/tested.**
   `default_output()`'s own return type (`dict[str, str]`) stays unchanged
   — `category_for_filename()` re-derives the category from the filename's
   own `.auto.tfvars.json`-stripped stem instead, since that stem already
   equals the category name 1:1 for every real case except `resx_<type>`
   (handled by its own `startswith` check). Confirm during implementation
   that no category name ever collides with this convention (e.g. nothing
   named `resx_something` that isn't actually a resource type).
1a. **Wiring point 2 is optional, not required — corrected.** Env vars
    never trigger the undeclared-variable warning (verified, see Proposed
    Design), so gating `deploy_controller.py`'s four delivery call sites
    does nothing for this doc's stated goal and is justified only on
    hygiene/consistency grounds. Decide explicitly whether to do it at all;
    if yes, `is_emitted()` is the shared gate and the sites are
    dns/networks/firewalls, the ten-category broadcast, `FLAT_CATEGORIES`'s
    per-key loop (gated once per category), and `merged_resources`. A
    further open question only applies if it *is* gated: **whether `emits`
    should behave identically at build and deploy time for a category whose
    *document* is unclaimed vs claimed** (gap #12's dns/networks/firewalls
    ownership concept) — not reasoned through yet.
2. **`format: strata|script|none` and `files: [...]` are not scoped here —
   deliberately.** Zero confirmed usage across all real workspaces checked
   (haven, `cfg-deployment` spoke/instance/control, `iac-deployment`); only
   `format: custom` + `emits` has real evidence. Build only that, same
   "evidence over assumption" call D2 itself already made once — narrowed
   this time, not reversed. Revisit if a real need for a script-generated or
   multi-source custom file shows up.
3. **Does `emits` interact with `resx_<type>`'s per-type file split?** v1's
   `EmitCategory.RESOURCES` is one value gating what v2 currently splits
   into N files (`resx_compute.auto.tfvars.json`,
   `resx_network.auto.tfvars.json`, ...). Proposed above: `emits:
   [resources]` gates all of them together, matching `merged_resources`'s
   existing deploy-time precedent (gap #8/#17) of treating every resource
   type as one logical category. Not yet confirmed against a real
   multi-resource-type `emits` example (the one real example, `control/
   workspace.yaml`, has no `resources` entry in its own `emits` list at
   all).
4. **Validation-time vs write-time rejection of an unknown `emits` entry.**
   `EmitCategory` being a closed enum means an unrecognized string is
   already rejected by Pydantic at document-load time (`strata validate`)
   — confirm this is sufficient, or whether `prepare()` also needs its own
   defensive check (the project's established "validated primarily at the
   model layer, defensive lookup elsewhere" pattern, e.g.
   `find_provisioner()`'s own docstring).
5. **No design yet for `ModuleModel.spec.output`'s equivalent** — Compose/
   Helm's own default-output gating, if it ever needs one. Out of scope:
   `ModuleSpecModel` has no `output` field at all yet (confirmed separately,
   this session), and Compose/Helm's own deploy-time value substitution is
   already tracked as separate, not-yet-designed work in ADR-0023's
   Remaining Work.
6. **Confirmed, real, and out of scope: `emits` has no key-level
   granularity within a category (see worked example 6a).** Opting
   `variables`/`flags`/`properties`/`custom` into `emits` delivers *every*
   declared key in that category, not just the ones the real `.tf` root
   happens to declare `variable {}` blocks for — adding one new variable
   that the root doesn't declare reintroduces the exact undeclared-variable
   warning this design exists to prevent, with no lever in this doc to stop
   it short of pulling the whole category back out of `emits`. The real fix
   is key-granularity Interface/Injection (ADR-0002), not yet built — do
   not attempt to add key-level filtering to `EmitCategory`/`emits` itself
   to compensate; that would blur the Structural/Environmental split
   ADR-0002 already deliberately drew on purpose.

## Implementation Plan

- [ ] `EmitCategory` enum + `OutputModel.format`/`.emits` fields +
      `validate_template_xor_emits()`, `provisioning_model.py`. Tests for
      the validator (both-set rejected, `emits` without `format` rejected,
      `template` alone still valid, `format`+`emits` alone still valid).
- [ ] `is_emitted()` + `category_for_filename()`, `terraform_projection.py`
      (next to `EmitCategory`/`FLAT_CATEGORIES`/`real_variable_name()` —
      one shared module owning the category vocabulary, not duplicated
      into `capabilities.py`/`deploy_controller.py`). Tests: unset `output`/
      unset `emits` emits everything (default unchanged), a filename's
      category round-trips for every real category including `resx_<type>`
      -> `RESOURCES`.
- [ ] Wire `is_emitted(category_for_filename(filename), provisioner.output)`
      into `InfraIntegration.prepare()`'s `default_output()` branch (wiring
      point 1). Tests: all 13 categories present without `emits` (unchanged
      default), a 3-category `emits` list suppressing the other 10, and
      `cfg-deployment`'s real example *translated* to v2's own naming
      (`emits: [flags, variables, properties]` — not `features`, see the
      confirmed naming-mismatch note above).
- [ ] **Decide between `emits` and env-var-only delivery (see "Alternative"
      above).** Blocking decision, not an implementation detail: option 1
      (rename files to non-auto-loaded `<category>.tfvars.json`) reaches
      zero warnings with no schema field at all, while `emits` reaches
      partial warnings permanently. The one prerequisite check (whether
      either real consumer's CI depends on auto-loading) is now **done** —
      verified against both `haven`'s and `cfg-int-deployment`'s real
      workflows directly: neither depends on it, `upload-artifact` operates
      on the whole `build/` directory, and `deploy run` already delivers via
      `TF_VAR_*` regardless. Nothing left blocking this decision.
- [ ] *(only if `emits` is still chosen)* Wire the `is_emitted()` gate into
      `deploy_controller.py`'s four env-var delivery call sites — now known
      to be **optional** (env vars never warn), justified on hygiene/
      consistency grounds only. Decide explicitly whether that's worth it
      rather than inheriting it from this doc's earlier wrong premise.
- [ ] Full check suite green (mypy, ruff, import-linter, pytest).
- [ ] Update this doc's Status to `current` and ADR-0023's own status line.

## Changelog

- 2026-10-05: Created. Scopes the revisit ADR-0023 D2 and
  `build-time-value-categories.md` both flagged as blocked-then-unblocked —
  port only the evidenced `format: custom` + `emits` combination, extending
  the existing `OutputModel` (D3) rather than adding a colliding sibling
  field. Not yet implemented.
- 2026-10-05: Found a real gap in this doc's own original design while
  checking how the undeclared-variable warning is actually avoided
  end-to-end: gating only `InfraIntegration.prepare()` (build time) is not
  sufficient — `deploy_controller.py`'s env-var delivery loop injects every
  category's `TF_VAR_x` unconditionally today, and Terraform treats an env
  var exactly like a `.tfvars` entry for this warning. Added wiring point 2
  (the deploy-time gate), a new open question (1a), and a worked example
  correction. Still not implemented — doc only, no code changed.
- 2026-10-05: Defined the gate concretely — `is_emitted()` +
  `category_for_filename()`, one shared pair in `terraform_projection.py`
  instead of four independent inline checks (`prepare()` plus
  `deploy_controller.py`'s three loops — found a fourth call site,
  `merged_resources`, while doing this, missed in the previous pass too).
  Added worked examples 2a-2d showing each real call site's exact gate
  placement. Resolves open questions 1/1a with a concrete proposal — still
  not implemented, doc only.
- 2026-10-05: Clarified that `emits` is hand-authored, never introspected
  from the real `.tf` root — a deliberate separation already decided in
  ADR-0002, not an oversight to fix later. ADR-0002 defines a parallel,
  not-yet-built concept ("Interface") that *does* parse real `variable {}`
  blocks via HCL (`python-hcl2`, already used by SBOM generation today), but
  explicitly scopes it to a different channel (Environmental: variables/
  features/secrets) and states Structural (`emits`) must not merge into it.
  Added ADR-0002 as a Related Decision.
- 2026-10-05: Confirmed a real, concrete granularity limit while tracing a
  second variable through example 6: `emits` gates whole categories, never
  individual keys within one — adding `display_name` alongside an already-
  emitted `customer_code` reintroduces the exact undeclared-variable warning
  this design exists to prevent, with no fix available inside this doc's
  scope (pulling the category out of `emits` loses `customer_code` too).
  Documented as worked example 6a and Remaining Work item 6 — the real fix
  is ADR-0002's key-granularity Interface/Injection, not an extension of
  `EmitCategory`. Still not implemented, doc only.
- 2026-10-05: **Corrected a wrong claim this doc introduced, by testing
  real Terraform 1.12.2 directly instead of asserting.** An undeclared
  variable supplied via `terraform.auto.tfvars.json` produces "Warning:
  Value for undeclared variable"; the same variable supplied via
  `TF_VAR_display_name` produces **no warning at all** — Terraform's own
  warning text explicitly recommends env vars as the way to silence it.
  Wiring point 2 (deploy-time env-var gating) is therefore **not** required
  for warning avoidance, contrary to this doc's previous two entries;
  demoted to an optional hygiene measure. Corrected wiring point 2, worked
  examples 2 and 6a, and the Implementation Plan.
- 2026-10-05: Added "Alternative: env-var-only delivery", prompted by the
  observation that a mechanism leaving residual ignorable warnings
  normalises ignoring warnings. Since only auto-loaded files warn, and
  `deploy run` already delivers all 13 categories via `TF_VAR_*` with
  fully-resolved values, simply renaming the build output to a
  non-auto-loaded `<category>.tfvars.json` reaches **zero** warnings with
  no schema field, no allowlist and no key-granularity gap — strictly
  better than `emits` at this doc's own stated goal. Found direct precedent:
  `write_resolved_manifest()` already chose plain YAML over
  `*.auto.tfvars.json` for exactly this reason. The `emits`-vs-alternative
  choice is now the blocking decision ahead of any schema work, since
  `emits` would be a permanent v2 schema field.
- 2026-10-05: Verified option 1 (the rename) directly against real
  Terraform 1.12.2 rather than assuming the auto-load convention's
  boundary. A non-auto-named `variables.tfvars.json` sitting next to a
  `.tf` root is **completely inert** — a plain `terraform plan` never reads
  it at all (confirmed: a declared variable with no other source came back
  as a missing-required-variable *error*, proving the file was never
  opened). The warning only reappears if something explicitly passes
  `-var-file=variables.tfvars.json` — which `deploy run` never does (it
  delivers via `TF_VAR_*` already). So the rename doesn't make the warning
  impossible in the absolute sense, it makes it opt-in: silent on every
  automated path, reappearing only for a human deliberately loading the
  file by hand, which is the correct behaviour in that case. Added this
  verification to option 1's description.
- 2026-10-05: Closed the remaining "Unverified" item blocking the
  `emits`-vs-alternative decision — read `haven`'s real
  `.github/workflows/deploy-infra.yml` and `cfg-int-deployment`'s real
  `.github/workflows/deploy.yml` directly. Both pipelines: `strata build
  run` → `upload-artifact`/`download-artifact` on the whole `build/`
  directory (no filename pattern) → `strata deploy run` (already injects
  `TF_VAR_*` as a direct subprocess env dict, confirmed by haven's own
  pipeline comment that this needs no pipeline-side wiring). The only raw
  `terraform` invocation in either pipeline is `terraform show` on an
  already-produced `.tfplan` binary — never a `.tfvars` file. Neither real
  consumer's CI depends on the auto-load convention anywhere. Updated the
  Implementation Plan's blocking item to reflect this is now resolved.
