# `SourceModel` — One `remote` Field, Declaration-Only Refs

- Status: implemented
- Date: 2026-09-22
- Related: [ADR-0015](0015-solution-manifest-and-document-discovery.md)
  (introduced `spec.remotes`, which this reference points at),
  [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (the same collapse-the-near-duplicates reasoning applied to Value tokens)

## Context and Problem Statement

`SourceModel` carried two fields that both answered *"which remote?"*:

```yaml
source: { repository: infra-repo, source_path: terraform/main }      # git
source: { chart_name: authentik, chart_repository: https://charts… } # chart
```

They differed only in what you selected afterwards. This is the same
near-duplicate shape already collapsed for `ModuleReferenceModel`
(ADR-0010/0011) and for the Value-token union (ADR-0002).

Worse, `chart_repository` held a **raw URL**. After ADR-0015 moved remotes
into the solution manifest, it was the last place in the schema that could
not be redirected upstream→internal-mirror by editing one declaration —
which is most of the reason named remotes exist.

`SourceModel` also had its own `reference` field, documented as *"Takes
precedence over the remote's default reference"*. A per-use-site ref
override lets two modules silently pull different trees of the same
repository, and makes "what version is deployed?" answerable only by
scanning every source in the solution.

Precedent checked rather than assumed. From Helm's own docs: a `Chart.yaml`
dependency's `repository` *"can be an alias. The alias must start with
`alias:` or `@`"*, with the name→URL mapping in a separate
`repositories.yaml`. Flux uses a single `sourceRef` spanning
`GitRepository`/`OCIRepository`/`HelmRepository`. Flux, Bazel and Nix all
pin the ref on the source declaration, not at the use site.

## Decision

**1. Collapse both fields into one `remote`**, and let the *selection* field
decide the mode:

```yaml
source: { remote: infra-repo,  source_path: terraform/modules/vpc }
source: { remote: goauthentik, chart_name: authentik, chart_version: "2024.12.0" }
```

Named `remote`, not `repository`, because it points at `spec.remotes` and a
remote may be a git repo, an OCI registry or a Helm index — "repository"
would be as narrow as the `CONTAINER`/`GITOPS` names ADR-0015 renamed.

**2. Mode comes from the selection field, not from `remote`.** `remote` is
mode-agnostic, so one OCI registry can serve charts in one source and plain
artifacts in another. Phase 1 still catches everything local: exactly one of
`source_path`/`chart_name`. Whether the named remote's `type` matches the
mode needs the loaded manifest — a Phase 2 check, parked with the others.

**3. `remote` is required for chart mode, optional for git mode.** A chart
always comes from a registry; a git source may legitimately be a path in the
solution's own repository, which is what omitting `remote` means. This
preserves the pre-existing behaviour where a bare `source_path` was valid.

**4. `SourceModel.reference` removed entirely.** A git/OCI ref is declared
once, on the remote, and never overridden per use site (ADR-0015 decision 3).

**5. `chart_version` stays at the use site — and this is not an
inconsistency.** A git/OCI remote at a ref *is* one immutable tree, so the
ref is part of its identity. A Helm remote is an index serving many
`(chart, version)` pairs, so picking one is *selection*, not remote
identity. The asymmetry is the point, and is stated in both docstrings.

**6. New guard: `chart_version` is rejected in git mode.** Both v1 and v2
previously accepted it silently — the same "validates fine, silently
ignored" class ADR-0071 (v1) and ADR-0016 target.

**7. Integration capability named `sources`, not v1's `repository`.** v1's
capability vocabulary is Protocol classes (`IRepositoryTool` in
`strata/models/capabilities.py`) with string names in YAML; its name for
this is `repository`. v2 uses `sources` for the same reason as the field
rename — a remote spans git, OCI and Helm. A deliberate vocabulary
deviation, documented inline in `VALID_INTEGRATION_CAPABILITIES`.

## Consequences

- Good: one field, one concept. The duplicate "which remote" question is
  gone, and an OCI registry used two ways is declared once.
- Good: chart sources gain the mirror/airgap redirect that git sources
  already had — no inlined URLs remain in the schema.
- Good: one remote resolves to exactly one tree per solution, so "what
  version is deployed?" is answerable from the manifest alone.
- Cost: **chart sources are no longer self-contained** — every chart needs a
  declared remote. Intended (it is what centralized redirect buys), and
  matches Flux, where a `HelmRelease` cannot inline a URL either.
- Cost: `chart_repository` loosened from `str` to `PlatformName`, since it
  now names a remote rather than holding a URL. A real constraint change,
  not just a rename.
- Neutral: the `@` sigil rule is now explicit — a bare name in a dedicated
  field (`remote:`), and `@name/` only where a remote name is embedded in a
  path string (`ModuleFileModel.source`), where the sigil is what
  distinguishes remote-qualified from solution-relative. Helm needs `@` even
  in its dedicated field only because that field also accepts raw URLs;
  ours does not.
- Cost: 1 model + 5 test files. One test (`test_module_source_rejects_mixed_git_and_chart`)
  was not merely renamed — it had worked by adding `repository` to a chart
  source, which under the new shape is no longer a mix, so it would have
  passed for the wrong reason. Reworked to mix the selection fields.
