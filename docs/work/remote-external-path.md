# Remote `path` for externally checked-out repositories - Work

- Status: draft
- Last updated: 2026-11-05

## Overview

A `fetch: external` remote ([solution_model.py](../../src/strata/models/solution_model.py) `SolutionRemoteModel`) says
"CI already cloned this". Strata still has to find the checkout, and today it does so by convention: it looks in
`.strata/remotes/<name>/<reference>`. A pipeline therefore has to know the ref, clone the repo, and then link the clone
to that conventional place.

This doc adds a second, explicit way to say the same thing: a remote names either a `reference` (as today) or a `path`
(strata looks there, no ref involved).

**What prompted it.** The `cfg-int-deployment` ADO pipeline, which checks out `env-int-deployment` and
`iac-int-deployment` and holds their refs in two places:

- `.azure/templates/pipelines/deploy-base.yml` declares `resources.repositories` with `ref: main` (env-int) and
  `ref: ${{ parameters.iacRef }}` (iac-int, set per pipeline).
- `strata.yaml` repeats both as `reference`, with a comment that they "must equal" the pipeline's. Nothing checks it.
- `.azure/templates/steps/link-remotes.yml` reads `strata.yaml` and symlinks `repos/<repo>` to
  `.strata/remotes/<name>/<reference>`, and `checkout-repos.yml` has to `rm -rf .strata/remotes` first because the
  reused agent workspace keeps stale links.

The pipeline cannot read the ref from `strata.yaml`: ADO resolves `resources.repositories` when the run is queued,
before any checkout. So the pipeline is the only place that can own the ref, and `strata.yaml` should not need to
repeat it.

## Current Design / Progress

- `resolve_remote()` ([remote_resolution.py](../../src/strata/controllers/remote_resolution.py)) computes
  `layout.remote_checkout_path(root, name, reference)`. If the directory exists it is returned **without being
  verified** ("this function's job is locating a checkout, not verifying one"). With `fetch: external` and no
  directory, it raises.
- `type: local` already resolves a solution-relative `url` as a path and never materialises it. It has no provenance
  (no URL, no commit) and cannot carry credentials.
- `audit_run.py` records every used remote as `ManifestRepositoryModel(url, ref, commit=None)`. `commit` exists in the
  model and is always `None`.
- A remote pin (Version document) on a `fetch: external` remote is already an error (`pin_not_applicable`,
  [version_pins.py](../../src/strata/controllers/version_pins.py)).

Phases 1 to 4 and the strata half of Phase 5 are built (see the plan). The cfg-int migration is not.

## Design

### The two modes

For `type: git`, a remote states **exactly one** of:

| Mode | Fields                                          | What strata does                                                                                              |
| ---- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Ref  | `reference` (and `fetch: strata` or `external`) | Exactly today: clone at the ref, or for `external` expect a checkout at `.strata/remotes/<name>/<reference>`. |
| Path | `path`                                          | Read the repository at that path. No ref, no clone, no `.strata/remotes` entry.                               |

```yaml
remotes:
  - name: iac-int            # path mode: the pipeline owns the ref
    type: git
    url: https://omp.ghe.com/OMP/iac-int-deployment.git
    path: repos/iac-int-deployment

  - name: shared-modules     # ref mode: unchanged
    type: git
    url: https://github.com/org/modules.git
    reference: v1.8.0
```

### Rules (schema)

1. `type: git` requires exactly one of `reference` and `path`. Both, or neither, is an error naming the remote.
2. `path` is only valid for `type: git`. (`local` already is a path; `oci` and `helm` have none.)
3. `path` **implies `fetch: external`**: nobody fetches into a path strata was told to read. An explicit
   `fetch: strata` together with `path` is an error. Writing `fetch: external` alongside is allowed but redundant.
4. `path` is relative to the solution root (the folder holding `strata.yaml`, not the directory strata was started in)
   and may use `..`, or be absolute. Only an option-like value (`-x`) and an empty one are rejected. It may be a
   symlink; it is only required to be a directory at resolve time. Leaving the solution is not reported: writing
   `path` is already the statement that someone else placed the checkout.
5. `integration` is rejected with `path`: strata never talks to the network for it, so credentials have no use.
6. `url` stays required. It is no longer used to find anything; it is provenance, recorded in the audit manifest. (A
   later change could make it optional in path mode. Not now: it keeps the manifest and `strata.yaml` honest about
   which repository this is.)

### Resolution

`resolve_remote()` gets one branch, next to `type: local`:

- `remote.path` set -> `(root / path)`. If it is not an existing directory, raise `RemoteResolutionError` naming the
  remote and the path, and saying that the pipeline's checkout step must place it there. The path is never created.
- Everything else is unchanged.

Callers that compute `remote_checkout_path()` themselves must go through the same branch. The ones found:
`controllers/remote_resolution.py:112` (the resolver), `controllers/build_controller.py` (sources),
`integrations/gitops.py` and `integrations/git_push.py` (push destinations, see below). Implementation starts by
listing them all with a grep and routing each through one helper, so no caller keeps its own copy of the rule.

### Audit: record the real commit (the "2" of the earlier discussion)

In path mode there is no ref to compare a checkout to, so the earlier idea of verifying `HEAD` against `reference` has
nothing to verify. The guarantee moves to **recording what was actually used**:

- `audit_run._collect_artifacts_bom()` resolves each used remote's directory and fills
  `ManifestRepositoryModel.commit` with `git rev-parse HEAD` of that directory. In path mode `ref` is `None`.
- Done for **path mode only** at first. For ref mode with `fetch: external` the same lookup is cheap and would make the
  manifest honest there too, but it changes existing manifests, so it is a separate decision (Q2).
- If the directory is not a git work tree (a copy, or `git` missing), `commit` stays `None` and a warning is logged.
  It never fails a deploy.

This turns "which iac-int did this deploy use?" from a question for the pipeline log into a manifest field.

### Consumers of `remote.reference`

A path remote has no `reference`. Each user of the field must either cope or refuse clearly:

| Consumer                                                       | Behaviour in path mode                                                                                                                                                                                         |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Remote version pins                                            | Already an error for `fetch: external`; covers path mode. Message mentions path.                                                                                                                               |
| `audit_run.py:552` (manifest)                                  | `ref=None`, `commit` filled as above.                                                                                                                                                                          |
| `version_controller.py:80`                                     | Only reads `fetch: strata` remotes. No change.                                                                                                                                                                 |
| GitOps / audit push (`reference` is the **branch to push to**) | A push target needs a branch, so a path remote cannot be one. Semantic check (`validate`) errors: "remote 'X' is a path remote; GitOps and audit sinks need `reference`". Found at `git_push.py`, `gitops.py`. |
| `audit_read.py` / `audit_push.py`                              | They keep their own checkout under `.strata/audit` and use `sink.branch`; they are not affected, but must not be pointed at a path remote either (same semantic check).                                        |
| `semantic_checks.py:477`                                       | Skips `fetch: external` remotes already; path mode is external. Re-check when implementing.                                                                                                                    |

### What the pipeline looks like afterwards (cfg-int)

- `strata.yaml`: both remotes use `path: repos/<repo>`; `reference` is removed from both.
- `link-remotes.yml` is deleted, and `install-tools.yml` no longer needs PyYAML for it.
- `checkout-repos.yml` keeps the two `checkout:` steps and drops `rm -rf .strata/remotes` (there is nothing left under
  it); it still removes `build`.
- The ref lives in exactly one place: `deploy-base.yml` (`main` for env-int) and each pipeline's `iacRef`.
- `discovery.exclude: repos/**` stays when the checkouts are inside the solution root; a checkout beside it needs no exclusion.

Developer workstations: with `path: ../iac-int-deployment` both sides use the layout they already have, because a
multi-`checkout:` Azure DevOps job puts each repository beside the others and developers clone siblings. A pipeline
that checks out into the solution (`repos/iac-int-deployment`) can keep that value, and a developer then links or
clones the repository there. The value is the same in both places only when both layouts match; see Q1.

## Decisions

- **D1 - Explicit and exclusive.** A git remote states a `reference` or a `path`, never both and never neither. No
  inference from `fetch`.
- **D2 - `path` implies `fetch: external`.** One fewer field to get wrong.
- **D3 - Path mode records the commit; it does not verify a pin.** There is no pin to verify.
- **D4 - No new verification in ref mode.** "A ref works as it does today" is kept literally, including trusting an
  existing checkout.

## Open questions

- **Q1 - Different paths in CI and on a workstation. Decided 2026-11-05: allow `..` and absolute paths.** The first
  answer was to do nothing and keep the path inside the solution, with an environment override
  (`STRATA_REMOTE_<NAME>_PATH`) as the later option. Allowing `..` needs no new mechanism and fits the layout
  Azure DevOps and developers already share (sibling folders). A `validate` warning for a path outside the solution
  shipped in 2.3.1 and was removed in 2.3.2 (see the Changelog). The environment override is still open for a layout
  where no single value works, with no evidence for it yet.
- **Q2 - Record the commit for ref-mode `fetch: external` remotes too?** Recommend: yes, in a follow-up, after path
  mode proves the lookup.
- **Q3 - Should a ref-mode external checkout be checked against its `reference`?** Not part of this change (D4).
  A warning when `HEAD` is not at the declared ref would be cheap, but a branch ref in a detached CI checkout makes
  "equal" ambiguous.
- **Q4 - Make `url` optional in path mode?** Recommend: later, only if the duplication is felt.

## Implementation Plan

Branch `feature/remote-external-path`. Test-first; each phase ends with `ruff check --fix`, `ruff format`, `mypy src`,
`lint-imports`, `pytest -q` green.

- [x] **Phase 1 - Schema.** `SolutionRemoteModel.path`; the six rules; `schema_rules` text; exported JSON schema; agent
  skill. Tests: each rule, both-or-neither error, `fetch: strata` + `path` error, `integration` + `path` error, an
  existing ref-mode document unchanged.
- [x] **Phase 2 - Resolution.** One helper for "where is this remote on disk", used by every caller found by grep.
  (Already true: every read goes through `resolve_remote()`, so the branch went there.)
  Tests: path found, path missing (message names remote and path), symlinked path, ref mode untouched, no checkout
  created under `.strata/remotes`.
- [x] **Phase 3 - Consumers.** Semantic check for GitOps and audit sinks on a path remote; pin message; the table above.
  Tests: each row.
- [x] **Phase 4 - Audit commit.** `commit` from `git rev-parse HEAD` in path mode; non-git directory warns. Real-`git`
  test (skipped when `git` is not on `PATH`).
- [ ] **Phase 5 - Docs and migration.** `docs/config/` remote page, `docs/design/remotes.md`; CHANGELOG `[Unreleased]`;
  then the cfg-int change (separate repo, separate PR): `strata.yaml`, delete `link-remotes.yml`, trim
  `checkout-repos.yml`. A minor release: optional field, no change for existing documents.

Out of scope: verifying ref-mode checkouts (Q3); an environment override for the path (Q1); `oci`/`helm` paths.

## Related Decisions

- [remotes.md](remotes.md) - `resolve_remote()` and the checkout convention this extends.
- [ADR-0019](../decisions/0019-version-pinning.md) - why checkouts are keyed by ref (not needed in path mode).
- [version-lifecycle.md](version-lifecycle.md) - `fetch: external` and pins.

## Changelog

- 2026-11-05: Created from a read of the cfg-int ADO pipeline and `resolve_remote()`. Nothing built.
- 2026-11-05: Path mode chosen as the way forward while strata cannot clone from omp.ghe.com in CI; the
  strata-fetched route (Version pins per deployment, `fetch: strata` with an `integration`) is the later target once
  that access exists, and needs no change here. Phases 1 to 4 and the strata docs built, tests first. Deviations:
  the existing "reference is required" message gained "unless it gives a 'path'" rather than a new wording; the pin
  message was left as is (a path remote is `fetch: external`, which it already names); the commit lookup requires
  `.git` in the folder itself, so a plain folder inside the solution's own repository does not report the
  solution's commit; audit pushes refuse a path remote at run time too, not only in `validate`. Branch: built on
  `feature/script-provisioner`'s working tree, not a branch of its own.
- 2026-11-05: Q1 decided and built as a patch (2.3.1) on `fix/path-remote-outside-solution`: `path` may use `..` or be
  absolute, resolved from the solution root by one helper (`layout.path_remote_location()`) that the resolver and the
  audit manifest both call; the schema no longer rejects them, and `strata validate` warns
  (`path_remote_outside_solution`) when the location is outside the solution. The environment override stays open.
- 2026-11-05: The `path_remote_outside_solution` warning removed again (2.3.2). It made `validate --strict` fail for
  every pipeline using the sibling layout it was built for, leaving a choice between dropping `--strict` (and the
  stale-pin guard) and filtering JSON. And it guarded nothing: writing `path` already says "I put it there", and the
  same pull request that changes `path` can add a script step that runs anything. A missing folder still fails
  `build run`. Untrusted pull-request builds are the pipeline's concern, not `strata.yaml`'s.
