# Effective images of a deployment - Work

- Status: implemented; follow-ups below
- Last updated: 2026-10-30

## Overview

A pipeline that deploys a Terraform root which pulls its container image from a registry needs to
know, before deploy, which images a deployment will run: the image name and the tag after any
`kind: version` pin. Example: copying each pinned image from a build feed into the spoke's
registry. Today it has to parse `strata.auto.tfvars.json` from the build output and know which
`strata.variables` keys are artifacts.

Three places answered "which images does this deployment use" and none was complete (see
[Current state](#current-state-before-this-work)). This doc covers one shared resolver, a read-only
command on top of it (`strata deploy images`), and moving the SBOM and the audit manifest onto it.

## Current state (before this work)

| Place                                                                        | Pins applied | `artifact` services and `store: artifact` included | Consumers                |
| ---------------------------------------------------------------------------- | ------------ | -------------------------------------------------- | ------------------------ |
| `sbom.json` (`sbom_controller._resolve_all_modules`)                         | No           | No (a service without `image` is skipped)          | CVE policy, image gate   |
| Audit manifest BOM (`audit_run._collect_artifacts_bom`)                      | Yes          | No (a service without `image` is skipped)          | Audit trail              |
| `resolve_artifact_services` and `store: artifact` (`resolve_artifact_field`) | Yes          | Yes                                                | Rendering, deploy values |

- The SBOM scanned the unpinned image list and the image gate gated on it. The chart collector read the
  unpinned chart version for the same reason.
- `resolve_artifact_field` returned only a string. The pin source (version document, `--pin`) was
  discarded. It also returned `None` both for an unknown artifact and for an artifact with no tag, so a
  `store: artifact` variable on a tag-less artifact with no pin failed `deploy run` with "not a known
  artifact".
- `build/<deployment>/resolved.yaml` and `strata.auto.tfvars.json` carry the pinned value of a
  `store: artifact` variable, but not the artifact name, the field, or where the tag came from.
- `values list` has no `--pin` and resolves every other store live. `promote status` lists pinned
  tags only, with no `image_name` and no fallback to the artifact's own tag.

## Current Design / Progress

Implemented. The shape:

### Command placement

| Option                                | Verdict                                                                                                                                                                     |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| New `strata artifacts` group          | No. A 14th top-level group with one subcommand, and the word is taken: `build run` "renders on-disk artifacts" and the audit manifest has an `artifacts` bill of materials. |
| `strata values list --type artifacts` | No. No `--pin`, only sees `store: artifact` variables, resolves other stores live.                                                                                          |
| `strata version ...`                  | No. That group manages version documents. A listing of effective pins across all five categories is a different question.                                                   |
| A file in the build output            | Works after `build run` without a new command, but needs a full build (source clones) and cannot take `--pin` on its own. Could be fed by the same resolver.                |
| **`strata deploy images DEPLOYMENT`** | Chosen. `deploy` is where the commands about a deployment live (`deploy list` already, and the surveyed `show`/`status` ones). Needs no build and no store access.          |

Named `images`, not `artifacts`: the kind is "a container image today, possibly other identifiers
later", and the consumer needs images. A later `deploy show` can include other identifiers.

There is no separate `deployment` group: `deploy list` replaced `deployment list` on 2026-10-30,
because `deploy` and `deployment` next to each other were easy to confuse and the only planned
member of `deployment` was this command.

### Shared resolver

`controllers/effective_images.py`:
`effective_images(context, deployment, *, pinned_by_flag=False) -> list[EffectiveImage]`. `deployment` is
the resolved model, already carrying a `--pin` in `spec.version`. `EffectiveImage` lives in
`integrations/resolved_context.py` so the SBOM collectors, which sit below the controllers, can read it.

Row fields:

- `origin`: `artifact` or `module-service`
- `name`; `artifact` and `module` where they apply; `via` (the variable key or service that uses it)
- `image_name`, `image_tag`, `image_ref`, `registry`, `configuration`
- `tag_source`: `pin`, `declared` or `none`
- `pin_version`: the version document that supplied the tag
- `pin_flag`: true when the tag came from `--pin`

Rows are ordered artifacts first, then module services, each in the order first used.

Rows cover only what the deployment reaches:

- artifacts named by `store: artifact` variables in its reachable environments
- artifacts used by enabled module services
- literal module service images, with `images` pins applied

An artifact nothing references is not listed.

### Provenance and blank tags

`resolve_artifact_tag()` in `value_controller.py` returns `ArtifactTag(value, source, version)`, or `None`
for an artifact that does not exist. `resolve_artifact_field()` calls it, so pin precedence stays in one
place. A blank tag is `source="none"`, and the `image_tag` field resolves to `""` (the same as an explicit
`image_tag: ""`), not `None`. That ends the misleading "not a known artifact" error: a deployment with no
pin and no tag now reaches the root module, whose own validation rejects the empty value. `image_ref`
becomes `name@algo:digest` for a digest tag (`compose_image_ref()`).

### SBOM and audit manifest

- `build run` computes `effective_images()` once and carries it on `ResolvedWorkspaceGraph.images`.
- The SBOM image collector adds the `artifact` rows to the module service images. An artifact image
  gets `strata:tag-stability=floating` when it has no tag, or a tag that is a known mutable alias
  (`latest`, `main`, ...). A tag a version pin chose is not floating just because it is a build number
  rather than a semantic version.
- `_resolve_all_modules()` applies the deployment's version pins, so module service image and chart
  versions in `sbom.json` are the deployed ones.
- The audit manifest's `artifacts.images` is built from `effective_images()`, so it includes artifact
  images.

### Command

`strata deploy images DEPLOYMENT [--pin X] [--path PATH] [-o json]`

- Never calls `resolve_values`: no store I/O, no secrets.
- Requires a valid solution, like `values list`. Exit codes: 0 listed, 2 bad arguments, an unknown
  deployment or `--pin`, or not inside a solution, 3 invalid solution, 1 system failure.
- Console: a table (name, origin, image, tag, source, used by). JSON: `{deployment, version, pin, images:
  [...]}`.
- A row with no tag is a warning (`image_without_tag`) in this command only. It does not fail.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) - version pins and their precedence
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - `kind: artifact` and `store: artifact`
- [ADR-0022](../decisions/0022-strata-build-run.md) - what `build run` renders, and the "build keeps
  tokens literal, deploy resolves" split

## Remaining Work / Open Questions

### Implementation plan

- [x] 1. Extract `resolve_artifact_tag()`; tests that `resolve_artifact_field()` behaves unchanged
- [x] 2. Add `effective_images`; tests for a `store: artifact` variable, a module service with
  `artifact`, a pinned literal image, an unreferenced artifact (excluded), a disabled module
  reference (excluded), a `--pin` override, a row with no tag
- [x] 3. Add `strata deploy images`; console and JSON tests
- [x] 4. Docs: `docs/config/artifact.md`, the CI how-to, the agent skill, the `cli.py` docstring
- [x] 5. Make `sbom.json` and the audit manifest BOM use the resolver

This is a new command, so steps 1 to 4 target 2.0.x or 2.1. Step 5 is a bugfix and can land in rc2.

### Open questions

- Literal module service images are listed with artifacts (filter on `origin`): decided yes, the SBOM and
  audit manifest need them too.

### Related follow-ups, not part of this work

- Guard rails (fail on no pin and no tag, reject floating tags): opt-in, design needed. Strata now
  resolves a tag-less artifact to `""` and relies on the root module to reject it; `deploy images` only
  warns. The row's `tag_source` and `is_mutable_alias_tag()` are the pieces to build on.
- A version document shared across workspaces (`spec.workspace` as a list): needs an ADR.
- `spec.mirror` on the artifact, and a capability check on `spec.integration`: defer until strata
  consumes either field.

## Changelog

- 2026-10-30: created from a consumer review of `kind: artifact` (Terraform roots, one workspace per
  product, image copy step before deploy).
- 2026-10-30: command placement changed from `deployment` to `deploy` when `deployment list` moved to
  `deploy list`.
- 2026-10-30: implemented steps 1 to 5. Also fixed: a `store: artifact` variable on an artifact with no
  tag and no pin reported "not a known artifact" and now resolves to an empty tag; a digest tag
  produced an invalid `name:sha256:...` reference.
