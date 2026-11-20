# Strata Supplies Input to IaC; It Does Not Rewrite IaC Source

- Status: accepted
- Date: 2026-09-25
- Related: [ADR-0022](0022-strata-build-run.md) (D3 — `sync_source()`'s
  materialisation contract, which this decision constrains),
  [ADR-0023](0023-build-output-rendering.md) (the `output.template` escape
  hatch this decision deliberately does *not* remove),
  [build-command.md](../design/build-command.md) /
  [workload-pipeline.md](../design/workload-pipeline.md) (where this shows up
  as "parity gap 4 — decided against")

## Context and Problem Statement

A v1-vs-v2 build comparison flagged that v1 performs template substitution
*inside the source files it copies*, and v2 does not. Re-verified directly in
v1's source before acting on it:

- `builders/base_builder.py` `_build_template_context()` assembles flat
  `STRATA_DEPLOYMENT_NAME`/`STRATA_WORKSPACE_NAME`/`STRATA_PROVIDER_{NAME}_*`
  keys plus nested `variables`/`features` dicts (constant + environment
  stores only; integration-backed stores are skipped at build time; secrets
  are deliberately excluded, since rendering them would write plaintext to
  disk).
- `builders/base_builder.py` `_apply_templates_to_dir()` then walks every
  file under the destination directory and Jinja2-renders it **in place**,
  using a lenient environment (`utils/templater.py`'s `_LENIENT_ENV`,
  `DebugUndefined` — a missing variable stays visible as `{{ var }}` rather
  than raising).
- `builders/terraform_builder.py` calls
  `self._apply_templates_to_dir(dest_dir, template_context)` immediately
  after every `shutil.copytree(...)` and after every pinned-ref
  `git archive` extraction. `helm_builder.py` does the same for a copied
  chart directory, passing `exclude_dirs={"templates"}`.

v2's equivalent (`controllers/source_sync.py`'s `sync_source()` /
`sync_module_source()`) is a byte-for-byte copy with no substitution pass.

The question this ADR settles: **should a materialised source file ever be
rewritten by strata after it is fetched?** It needs an explicit answer
because the surrounding decisions do not give one — ADR-0022 D3 defines
*where* a source lands but never whether its bytes may be modified, and
ADR-0023 Phase 3 scopes token resolution to
`provisioner.backend`/`.configuration`/`.properties`, never naming the
copied-file surface at all. Left unstated, the omission reads as "not built
yet" rather than "deliberately not done".

## Considered Options

- **Option A — port v1's behaviour.** Add a template-substitution pass to
  `sync_source()`/`sync_module_source()`, porting `_build_template_context()`
  and `_apply_templates_to_dir()` (needing a new `strata/utils/templater.py`
  with a lenient Jinja2 environment).
- **Option B — sources are opaque; strata only supplies input.** A
  materialised source is copied verbatim, always. Deployment-specific values
  reach the tool exclusively through that tool's own native input mechanism,
  which strata *generates alongside* the source rather than injecting into
  it.

## Decision Outcome

Chosen: **Option B**, because every tool strata drives already has a
first-class input mechanism designed for exactly this, and rewriting fetched
source is both redundant with it and actively worse.

**The principle, stated once:** strata supplies *input to* infrastructure-as-code;
it does not rewrite the code itself. Concretely:

| Tool | Native input mechanism strata writes | Never touched |
| --- | --- | --- |
| Terraform | `*.auto.tfvars.json` (typed, auto-loaded), `TF_VAR_*` | the `.tf` source |
| Helm | `values.yaml` (+ deploy-time `--set-string` for secrets) | the chart, incl. its `templates/` |
| Compose | `docker-compose.yml` generated from `spec.services`; `${KEY}` + `.env` at deploy time | a vendored compose file |
| Scripts/other | `STRATA_*` environment variables | the script |

Three reasons, in order of weight:

1. **The native mechanism is strictly better, and already built.**
   `terraform_projection.py` already writes real, typed, Terraform-auto-loaded
   `.auto.tfvars.json` files. That is a *supported interface* with a stable
   contract; text-substituting into `.tf` source is neither, and can produce
   syntactically invalid HCL from a value that a `.tfvars.json` file would
   have carried losslessly.
2. **A materialised source is frequently vendored, third-party content** — a
   shared Terraform module, a chart pulled from a registry — that the
   deployment does not own. Mutating someone else's code in place after
   fetching it is the wrong layer, and becomes a real injection surface the
   moment a remote serves content the solution doesn't fully control. The
   native-input path has no equivalent exposure: a value lands in a data file
   the tool parses as data, never as code.
3. **v1's own implementation shows the strain.** `_apply_templates_to_dir()`
   has to silently skip "files not parseable as a Jinja2 template (e.g.
   Helm's own Go-templates, `{{ .Release.Name }}`)", and `helm_builder.py`
   has to pass `exclude_dirs={"templates"}` on top of that. A
   rewrite-everything pass needing a permanent carve-out to avoid corrupting
   content it should never have touched is the design telling on itself.

**Scope boundary — what this ADR does *not* forbid.** This is a constraint on
*materialised source files only*. It leaves untouched:

- Every artifact strata *generates* (`.auto.tfvars.json`, `values.yaml`,
  `meta.yaml`, `docker-compose.yml`) — strata owns those files completely.
- ADR-0023's `output.template` escape hatch (Phase 4, not built). That
  renders a **new** file the deployment itself declares — generation, not
  mutation — and should use *strict* semantics (a missing variable raises)
  rather than v1's lenient "leave `{{ var }}` visible" behaviour, which turns
  a typo into a silently malformed artifact.
- `ModuleFileModel` (`module.spec.files`) copying extra files into a module's
  build output. Those are deployment-owned files, not fetched source; whether
  they get substitution is a separate question this ADR does not answer.

### Consequences

- Good: `sync_source()`/`sync_module_source()` stay trivially simple and
  auditable — a copy is a copy. No Jinja2 dependency on the materialisation
  path, no "which files are safe to render" carve-out list to maintain, no
  class of bug where a build silently corrupts a vendored module.
- Good: the integrity of a fetched source is preserved end to end, so a
  pinned ref (ADR-0019) means the bytes on disk actually match the pin —
  which is what makes the pin worth having.
- Good: forces deployment-specific values through a typed, tool-supported
  interface, where a wrong value fails loudly at the tool's own validation
  rather than producing mystery syntax errors in rewritten code.
- Bad: a solution migrating from v1 that *relies* on `{{ STRATA_* }}`
  placeholders inside its own committed `.tf` files must convert them to
  Terraform variables fed by `.auto.tfvars.json`. Real migration cost, not
  hypothetical — though such a file is already unusable by `terraform` on its
  own (it isn't valid HCL until strata rewrites it), which is itself an
  argument for the conversion.
- Bad: a genuinely source-level parameterisation need that no tool-native
  mechanism covers would have no answer under this decision. None was found
  across the real consumers checked; if one appears, `output.template`
  (generate a new file) is the intended escape hatch, and superseding this
  ADR is the path if that proves insufficient.
