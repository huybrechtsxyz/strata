# Documentation-generation tooling integration (terraform-docs, helm-docs)

- Status: proposed
- Date: 2026-09-17

## Context and Problem Statement

strata's build phase already produces real, structured infrastructure-as-code
artifacts that downstream tools can introspect without any strata-side changes:

- `TerraformBuilder` copies live Terraform source (`.tf` files: `variables.tf`,
  `outputs.tf`, provider blocks, etc.) into
  `build/{deployment}-{version}/terraform/{provisioner}/`
  ([src/strata/builders/terraform_builder.py](../../src/strata/builders/terraform_builder.py)).
- `HelmBuilder` writes `values.yaml` + `meta.yaml` per module into
  `build/{deployment}-{version}/{namespace}/{module}/`
  ([src/strata/builders/helm_builder.py](../../src/strata/builders/helm_builder.py)).

Neither of these directories currently gets a generated human-readable README —
teams either hand-write module docs (which drift from the actual variables/values)
or run `terraform-docs`/`helm-docs` themselves outside of strata, with no connection
to strata's build lifecycle or manifest.

`docs/config/workflow.md` and ADR-0049 (workflow-as-executable-runbook, still
`proposed`) already use `terraform-docs markdown . > README.md` as the illustrative
example for a project-specific `.strata/workflow.yaml` step — but that's only an
example of the *syntax*; no actual check/command wiring exists today, and the
underlying shell-form `check:` feature ADR-0049 depends on hasn't been implemented
either.

This ADR looks into whether/how documentation-generation tooling should become a
first-class (or at least documented) part of strata, separate from ADR-0049's more
general workflow-runbook mechanism.

**Tools surveyed:**

| Tool                                                     | What it does                                                                                                      | Input strata already produces                                                                                                                                                                                                                       |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [terraform-docs](https://terraform-docs.io)              | Generates a markdown table of inputs/outputs/providers/resources from `.tf` files                                 | `build/.../terraform/{provisioner}/` (real HCL, copied by `TerraformBuilder`)                                                                                                                                                                       |
| [helm-docs](https://github.com/norwoodj/helm-docs)       | Generates a README from `values.yaml` (+ optional comment annotations) and `Chart.yaml`                           | `build/.../{namespace}/{module}/values.yaml` (written by `HelmBuilder`)                                                                                                                                                                             |
| PSRule for Azure (`PSRule.Rules.Azure`) + `PSDocs.Azure` | Analyzes **ARM/Bicep** templates against the Well-Architected Framework and can render markdown from rule results | **No direct input** — strata's Azure provisioning goes through the `terraform` provisioner type (see `config/azure-aks/stack/`), not Bicep/ARM. ADR-0046 covers a Bicep provisioner as a *possible future alternative*, but it doesn't exist today. |

PSRule/PSDocs.Azure is included here only to record that it was evaluated and
rejected as out of scope for now — it parses Bicep/ARM syntax, not Terraform HCL or
strata's `platform.json`, so there's no artifact today for it to consume. Revisit if
ADR-0046 (Bicep provisioner) is ever implemented.

## Considered Options

### Option A — Status quo: leave it to team CI / `.strata/workflow.yaml` hints

Teams document the `terraform-docs`/`helm-docs` invocation themselves, either in
their own CI pipeline or as a `command:`-only step in `.strata/workflow.yaml` (no
`check:` wiring, just a hint per the existing dynamic-step pattern).

- Good: zero strata code changes; both tools are already standalone CLIs that need
  no strata awareness.
- Bad: no connection to strata's build lifecycle — docs can silently go stale with
  no signal in `strata guide`/`strata build run`; every team re-solves the same
  "where do I put this" question independently.

### Option B — Depend on ADR-0049's shell-form workflow checks

Wait for ADR-0049 (`proposed`, not started) to land shell-form `check:`/token
substitution, then ship `terraform-docs`/`helm-docs` steps as **documented
workflow.yaml examples** (already partially done in `docs/config/workflow.md`) —
no new strata code beyond what ADR-0049 already requires.

- Good: reuses a general mechanism instead of building a special case; the example
  already exists in the docs.
- Bad: blocked entirely on ADR-0049 shipping first; doesn't give strata any
  native visibility into doc staleness (still just a shell command someone has to
  remember to run).

### Option C — First-class integration (same pattern as Checkov/Infracost)

Build a soft-dependency `integrations/terraform_docs.py` (and/or `helm_docs.py`)
following the pattern already established by `InfracostIntegration` and the Checkov
integration (ADR-0051): detect the CLI on PATH, invoke it against the build output
directory, cache results keyed off artifact hash, and optionally record a
`docs_generated` reference in the deployment manifest.

- Good: native `strata build sbom`-style command (e.g. `strata build docs`);
  staleness becomes checkable (`diff` mode) the same way Checkov/Infracost are;
  consistent with strata's existing soft-dependency + graceful-degradation pattern.
- Bad: two more CLI dependencies to detect/version-track (`strata tools status`
  surface grows); scope duplicates part of what ADR-0049's generic shell-check
  mechanism would already provide for *any* external tool, not just these two.

## Decision Outcome

**Not yet decided — this ADR exists to record the survey and keep the options
open for a follow-up decision.** Leaning towards **Option B** (ride on ADR-0049's
generic shell-form workflow checks rather than hand-building a special-cased
integration) since `terraform-docs`/`helm-docs` are simple, single-purpose CLIs
that don't need caching/resource-mapping the way Checkov's structured findings do
— but this should be revisited once ADR-0049 has a concrete implementation status.

## Remaining Work

- Decide between Option B and Option C once ADR-0049's shell-form checks either
  ship or are deprioritized.
- If Option C is chosen: design cache-invalidation (artifact hash) and manifest
  fields, mirroring `SbomReferenceModel`.
- If Option B is chosen: promote the existing `docs/config/workflow.md` example
  from illustrative-only to a verified, tested example once ADR-0049 ships.
- No code changes have been made as part of this ADR.
