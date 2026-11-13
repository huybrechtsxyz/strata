# Image tag policy (guard rails) - Work

- Status: implemented; follow-ups below
- Last updated: 2026-11-01

## Overview

Strata resolves the tag of every image a deployment runs (`strata deploy images`, see
[effective-images.md](../design/effective-images.md)) but never judges it. An artifact with no tag and no
`kind: version` pin resolves to an empty tag, and an image tagged `latest` is accepted. Whether that is
acceptable is left to whatever consumes the value, for example a Terraform root that rejects an empty tag. The
failure then shows up late, at `terraform plan`, and says nothing about which image or which pin is missing.

This doc covers an opt-in workspace policy that fails (or warns) when an image a deployment runs has no tag,
has a floating tag, or does not match a required tag shape. It follows the existing policy framework
([ADR-0049](../decisions/0049-strata-policy-command-group.md)): one workspace field, one evaluator, one row in
the `strata policy` table, recorded in the audit manifest. The user-facing description is "Image tag policy" in
[docs/config/workspace.md](../config/workspace.md).

Not in `docs/design/` yet: it graduates when the maintainer is ready to settle its shape.

## Current state (before this work)

Verified against the source on 2026-11-01.

| Where                                  | What it does today with a missing or floating tag                                                                            |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `resolve_artifact_field` (`image_tag`) | A tag-less artifact with no pin resolves to `""`. Nothing is reported.                                                       |
| `store: artifact` + a Terraform root   | The root's own validation is the only guard, so the failure appears at plan.                                                 |
| `strata deploy images`                 | A row with no tag is a warning (`image_without_tag`). It never fails, and `latest` is not reported.                          |
| SBOM image collector                   | Sets `strata:tag-stability=floating` and a warning. A pinned artifact tag is floating only when it is a known mutable alias. |
| `strata validate`                      | Nothing. Whether a tag exists depends on the deployment and on `--pin`, which `validate` does not know.                      |
| `strata version update --check`        | Does not offer an artifact with no tag as a pin to add, because there is no value to seed it with.                           |

Pieces that already exist and are reused:

- `effective_images()` returns one row per image with `tag_source` (`pin`, `declared`, `none`), `image_tag`,
  `origin` and `via`.
- `is_mutable_alias_tag()` and the alias set (`latest`, `main`, `master`, `dev`, `develop`, `staging`, `edge`,
  `nightly`, `canary`) in `utils/sbom_utils.py`.
- The policy framework: workspace fields `cve_policy`, `checkov_policy`, `release_gate_policy`,
  `image_gate_policy`, each with `enforcement: deny | warn`, one row in `policy_controller._POLICIES`,
  `policy_results.json` and `ManifestPolicyResultModel` for the audit record.

## Current Design / Progress

Implemented. The shape:

### Where the opt-in lives

| Option                                           | Verdict                                                                                                                                                                                                       |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A workspace policy field, `image_tag_policy`** | Chosen. It is the established place for a guard rail, it is per workspace (one workspace per product), and it gets `policy list/check`, results and the audit record for free. Absent means today's behavior. |
| A flag on `kind: artifact` (`require_tag`)       | No. It covers only artifacts, not module service images, and scatters one rule over many documents.                                                                                                           |
| `Configuration.spec.security`                    | No. It is solution-wide and has no phases or enforcement levels.                                                                                                                                              |
| A section of `kind: version`                     | Not now. A version document is per ring, which is attractive (see per-ring strictness below), but it mixes pins with policy and a deployment without a version would have no policy.                          |
| `strata validate`                                | No. The effective tag depends on the deployment and on `--pin`, so a document-level check cannot know it.                                                                                                     |

The name is `image_tag_policy`, not `image_policy`, to stay clear of `image_gate_policy`, which gates on a
vendor's open issues for the images in `sbom.json`.

### The policy

```yaml
kind: workspace
spec:
  image_tag_policy:
    enforcement: deny            # deny (default) | warn
    origins: [artifact]          # artifact | module-service; default: both
    require_tag: true            # default true
    reject_tags: [latest, main]  # default: the mutable alias set
    tag_pattern: '^\d+\.\d+\.\d+$'   # optional: a tag must match (searched, so anchor it)
    require_pin: false           # optional: the tag must come from a kind: version pin
    rings: [acc, prd]            # optional: only deployments in these rings; default all
    allow:
      - name: redis              # a row name (artifact or service)
        reason: "third-party image, covered by cve_policy"
        expires: 2026-12-31      # optional ISO date
```

`image_tag_policy: {}` is a useful policy: a tag is required and the mutable aliases are rejected.

Rules, evaluated per row of `effective_images()` whose `origin` is selected and whose name is not allowed:

| Rule          | Fails when                                    | Code                 |
| ------------- | --------------------------------------------- | -------------------- |
| `require_tag` | `tag_source` is `none`                        | `image_without_tag`  |
| `reject_tags` | the tag is in the list (case-insensitive)     | `image_floating_tag` |
| `tag_pattern` | the tag does not match the regular expression | `image_tag_pattern`  |
| `require_pin` | `tag_source` is not `pin`                     | `image_not_pinned`   |

- A digest (`name@sha256:...`) is immutable: it passes `reject_tags` and `tag_pattern`, and still has to satisfy
  `require_tag` and `require_pin`.
- A tag that merely is not semantic-version shaped (a build number such as `env_sbx_20260907.11`) is not
  floating. `reject_tags` is an explicit list, and `tag_pattern` is the way to be stricter. This matches how the
  SBOM treats a pinned tag.
- Each finding names the row, its tag source and `via` (the variable or service that uses it), so the fix is
  obvious: which pin to add, in which version document.
- Severity follows `enforcement`: `deny` is an error and stops the run, `warn` is a warning.
- There is no `on_missing_data`: the rule reads only the solution, so there is no data that can be missing.

### When it is evaluated

- **`build run`**, right after the integration preflight and before anything is cleaned or rendered, so a
  violation fails fast with nothing written. It runs in `--dry-run` too, because it is offline. The result is
  added to `policy_results.json` with phase `build`.
- **`deploy run`**, in the preflight before the steps, with the deployment's effective images. The tag in effect
  at deploy can differ from the build's (`--pin` given only to `deploy`), and artifacts resolve at deploy. The
  result is recorded with phase `deploy` and replaces the build's entry for the same policy.
- **`strata policy check`**: one new row in `_POLICIES`, `check` only (no itemised findings beyond the
  diagnostics), phase `build`. `--pin` is honored.
- **`strata deploy images`**: when the workspace configures the policy, its findings are reported and `deny`
  exits 3, so the image copy step at the start of a pipeline fails first. Without the policy the command keeps
  warning on a missing tag and exits 0.
- **`strata validate`** does not evaluate it.

Results use `ManifestPolicyResultModel` as the other policies do: `policy_name: image_tag_policy`,
`policy_type: image_tag`, `phase`, `enforcement`, `passed`, and one violation message per finding. The audit
manifest picks them up from `policy_results.json`.

### Per-ring strictness

A workspace is shared by every ring of a product (dev, tst, acc, prd), and a workspace field cannot differ per
ring. A team will want `deny` on acc and prd, and a dev ring that may float. Options:

| Option                                                    | Notes                                                                                                                                                                                   |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Workspace-wide only                                       | Simple. One setting for every ring, so dev has to be as strict as prd, or the policy is `warn` everywhere.                                                                              |
| **`rings: [acc, prd]` on the policy**                     | Proposed. The deployment's ring is `spec.promotion.ring` of its version document, which already exists. A deployment with no ring is evaluated (fail closed).                           |
| A per-deployment override (`deployment.spec.policies...`) | Most flexible, but it is a new mechanism that would apply to every policy and cuts across the "rebind only, no field patches" rule in [workspace-overrides.md](workspace-overrides.md). |

## Related Decisions

- [ADR-0049](../decisions/0049-strata-policy-command-group.md) - `strata policy` and the dispatch table; adding a
  policy is one field, one evaluator, one row
- [ADR-0047](../decisions/0047-deploy-security-gates-as-vendor-neutral-capabilities.md) - the deploy-time gates
  and the `deny | warn` convention
- [policy-engine-architecture.md](policy-engine-architecture.md) - bespoke policies, no generic engine
- [ADR-0019](../decisions/0019-version-pinning.md), [ADR-0026](../decisions/0026-pinnable-artifact-references.md) -
  pins and artifacts
- [effective-images.md](../design/effective-images.md) - the resolver this policy reads

## Remaining Work / Open Questions

### Implementation plan

- [x] 1. Model: `ImageTagPolicyModel` and `WorkspaceSpecModel.image_tag_policy`, with `schema_rules`, a schema
  example, and model tests (defaults, an invalid `tag_pattern`, an `allow` entry without a reason). The model
  carries every field, including `rings` and `require_pin`, so the schema does not change between phases
  (`src/strata/models/image_tag_policy_model.py`)
- [x] 2. Evaluator: a pure `evaluate_image_tag_policy(images, policy, ring)` returning `Diagnostics`, plus the
  result builder in `policy_results.py`; tests for each rule, a digest, `allow`, `origins`, both enforcement
  levels, and `rings`. Also built here: `check_image_tag_policy(context, deployment, workspace)` (the entry point
  phase 3 calls) and the `strata policy` row, because a test requires one row per `*_policy` field
  (`src/strata/controllers/image_tag_policy.py`)
- [x] 3. Wiring: `build_run` (early, including `--dry-run`, and the result in `policy_results.json`),
  `deploy_run` preflight, and `deploy images` enforcement (the `_POLICIES` row and `policy list` were done in
  phase 2)
- [x] 4. Docs: the workspace reference, the agent skill, the CI how-to, and the `Current state` table above
- [x] 5. Evaluator support for `rings` and `require_pin`: done in phase 2, nothing left

New capability, so 2.0.x or 2.1, not rc2.

### Open questions

Decided by building it: the name is `image_tag_policy`; per-ring strictness is a `rings` selector; both origins
are covered by default and `origins` narrows it; "floating" is the explicit alias list unless `tag_pattern` is
set; `allow` entries need a `reason` and may have `expires`; `require_pin` is in; `deploy images` fails on
`deny` when the policy is set; `version update` reports an artifact with no tag and no pin as needing a version.

Nothing is open.

### Risks

- A policy that is too strict by default trains people to switch it to `warn`. Hence an explicit alias list as
  the default and no semver requirement unless `tag_pattern` is set.
- Evaluating at both build and deploy can disagree when `--pin` differs. The deploy result replaces the build
  result for the same policy, so the audit record shows what was in effect at deploy.
- `policy_results.json` is overwritten by `build run` each time, so the image tag result must be part of the list
  `build run` writes at the end, not appended earlier.

## Changelog

- 2026-11-01: created. Follows the "guard rails" item left open in [effective-images.md](../design/effective-images.md).
- 2026-11-01: phase 1 (model). Decided for the model: `rings`, `require_pin` and `allow[].expires` are in the
  first cut; `reject_tags` omitted means the alias set and `[]` means none (stored lower-case); `tag_pattern` is
  searched, not full-matched, and must compile; `allow` names are unique; `on_missing_data` is not a field.
- 2026-11-01: phase 2 (evaluator). Decided: `rings`, `require_pin` and `allow[].expires` are evaluated now, so
  phase 5 is empty. A digest passes `reject_tags` and `tag_pattern` but still has to satisfy `require_pin`. An
  image with no tag gets one finding (`image_without_tag`), not also `image_not_pinned`, unless `require_tag` is
  off. `allow` matches the row name and is honoured through its `expires` day; an expired entry no longer
  exempts the image and its finding says so. A ring outside `rings` gives one info finding
  (`image_tag_policy_not_applied`) and the result is recorded as passed. `policy check` runs offline in phase
  `build`, honours `--pin`, and has no `policy findings` row. `MUTABLE_ALIAS_TAGS` is now public in
  `sbom_utils`.
- 2026-11-01: phase 3 (wiring). `build run` resolves `effective_images()` once, right after the integration
  preflight, judges them before anything is cleaned or written (so a `deny` breach leaves the build path as it
  was, including under `--dry-run`), and reuses the rows for the graph. Its result goes into the list written at
  the end of the build, so a failed build records none. `deploy run` judges the deployment's images before it
  resolves any value (offline, so also under `--smoke-test`) and replaces the build's entry with a `deploy`
  phase one when `build_path` exists. `deploy images` uses the policy instead of its own missing-tag warning
  when the workspace sets one, and exits 3 on `deny`. `check_image_tag_policy()` takes `images=` so callers
  that already hold the rows do not resolve them twice.
- 2026-11-01: phase 4 (docs). Documented in the workspace reference ("Image tag policy"), the agent skill, the CI
  how-to and the schema overview. The old `Current state` table is now headed "(before this work)". Open:
  `version update --check`; graduate this doc to `docs/design/` once that is settled.
- 2026-11-01: `version update` now reports an artifact with no `image_tag` and no pin as `needs_value` (finding
  `pin_needed`: a warning, an error under `--check`), so the version document gate and this policy agree. It is
  never seeded. This reverses the 2026-10-06 rule that such an artifact is dropped from the candidates; the rule
  still holds for a module service with no `image`, a chart with no version, and the other categories. The doc
  can graduate to `docs/design/`.
