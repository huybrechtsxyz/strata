# Helm registry charts fetched at build - Work

- Status: in-progress (implemented; open questions remain)
- Last updated: 2026-11-07

## Overview

Gap 8 of [helm-gap-analysis.md](helm-gap-analysis.md), designed under
[ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md) (`build run` may fetch what a release
deploys, never where it deploys).

A Helm module's chart is either local (copied into the build directory by `build run`) or from a registry
(`chart_name`). A registry chart is only described in `meta.yaml`, and `helm upgrade` fetches it at deploy. So:

- the configuration key check and `helm lint` both skip it, and a misspelt `configuration` key is silently ignored
  by Helm (haven has 6 registry charts);
- the SBOM records the declared `chart_version`, which may be unset or a range, and nothing inside the chart;
- an unreachable registry or a missing version fails the deploy mid-way, after earlier steps have applied;
- with no exact `chart_version`, the deploy installs whatever the registry serves that day.

The fix: `build run` pulls each registry chart into `.strata/helm/`, records its exact version and digest in
`meta.yaml` (the chart is then **pinned**), and checks it like a local chart. The deploy installs that recorded
chart, verified against the digest, from a local archive, and refuses a chart the build could not pin.

## Current state

Verified against the source on 2026-11-07.

| Concern                    | Local chart                                         | Registry chart today                         |
| -------------------------- | --------------------------------------------------- | -------------------------------------------- |
| Where the chart is         | `build/<namespace>/<reference>/`                    | nowhere until `helm upgrade` fetches it      |
| Configuration key check    | `build run` (`workload_controller.py`)              | skipped                                      |
| `helm lint`                | check phase (`check_namespace()`, ADR-0043)         | skipped                                      |
| SBOM                       | `Chart.yaml` and its `dependencies`                 | declared name and version only               |
| What `helm upgrade` gets   | the directory                                       | `alias/chart` or `oci://…/chart` + `--version` |

- `_render_meta()` writes `chartName`, `chartVersion` (as declared), and `chartRemote` or `chartRepository`.
- The deploy resolves the chart reference with `_resolve_chart()` (`repo add --force-update` for HTTP, `registry
  login` for OCI) and authenticates with ADR-0042's `RegistryCredentials`, read from the deploy's resolved values by
  `_chart_registry_credentials()`. Only credentialed modules get an isolated Helm configuration; an anonymous HTTP
  repository is added to the user's own.
- Build-time credentials exist for git only: `RemoteCredentialResolver` (`remote_credentials.py`) looks up the
  remote's integration, requires the `sources` capability and a git method (`token`, `ssh_key`, `cli`), and resolves
  only keys from stores a plain build may read (`--resolve` widens that). Registry integrations use the same
  `sources` capability (ADR-0042).
- `build run` does not need `helm` today, and strata's image ([Dockerfile.cli](../../Dockerfile.cli)) does not
  ship it.

## Design

### The cache

```
.strata/helm/<source>/<chart>/<version>/
    <chart>-<version>.tgz     # the archive as pulled
    <chart>/                  # the archive unpacked
```

- `<source>` is the remote name for `chartRemote`, and `_repo_alias(url)` for an inline `chartRepository` (a
  readable prefix plus a hash of the URL, already collision-free).
- `<version>` is the exact version Helm resolved, never the declared range.
- The digest is the SHA-256 of the `.tgz`, computed by strata, so a cache hit needs no `helm`. It is the same for
  HTTP and OCI and is a CycloneDX `SHA-256` hash.
- A pull lands in a temporary directory under `.strata/helm/` and is renamed into place, so an interrupted pull
  leaves no partial entry. The archive is unpacked by strata (`tarfile`, `data` filter). Removing the temporary
  directory is best effort: on Windows a virus scanner can hold the new archive open, and that must not replace the
  pull's result. A leftover `.pull-*` directory is never read as an entry. A rename that fails is
  `helm_chart_pull_failed`.

### `build run`: pin, record, check

For each enabled registry-chart module, during materialisation (where `sync_module_source()` runs today):

1. **Cache hit.** An exact `chart_version` with a cache entry is pinned from the cache. Nothing is fetched.
2. **Pull.** Otherwise, `helm pull <ref> [--version <chart_version>]` in an isolated Helm configuration
   (`_isolated_helm_env()`) for every registry, credentialed or not, so the build never touches the user's Helm
   configuration. The exact version comes from the pulled `Chart.yaml`. A range or an unset version is pulled on
   every build, because only the registry knows what it resolves to today. If the entry for that version already
   exists with a different digest, the build fails with `helm_chart_digest_changed`.
3. **Cannot attempt the pull** (ADR-0054): `helm` is not installed, the credentials are in a store a plain build does
   not read (no `--resolve`), or `--offline` was given. The chart is left **unpinned**: a `helm_chart_not_pinned`
   warning, and `meta.yaml` keeps the declaration as today.
4. **Pull failed** (no such chart or version, login rejected, unreachable): `helm_chart_pull_failed` or
   `helm_registry_auth_failed`, an error.
5. **Record.** For a pinned chart, `meta.yaml` gets:

   ```yaml
   chartName: authentik
   chartVersion: 2026.5.2              # resolved, exact
   chartVersionConstraint: ^2026.5.0   # only when the declared version differs
   chartDigest: sha256:9f86d0…
   chartAppVersion: 2026.5.2           # from Chart.yaml, when it has one
   chartSubcharts:                     # charts/ in the archive, exact versions
     - {name: postgresql, version: 15.5.38}
   chartRemote: goauthentik
   ```

   The build output is self-contained: the SBOM reads `meta.yaml` and never the cache.
6. **Check keys.** `check_configuration_keys()` against the pinned chart's `values.yaml`, as for a local chart, with
   the same `helm_unknown_configuration_key` warning.

`build run --dry-run` fetches nothing and reports `would pull chart 'authentik' (^2026.5.0) from 'goauthentik'`, or
`would use cached chart …` for a hit.

`build run --offline` (new): no fetch at all, so a build never uses the network for charts. Cache hits are pinned,
everything else is unpinned with a warning. Git sources are unaffected by the flag for now (ADR-0054's follow-up).

### Credentials at build

`RemoteCredentialResolver` splits into the shared part (find the integration, check it is enabled and declares
`sources`, resolve the keys its authentication references under the store rule) and the per-use checks:

- git (`for_integration()`): a git method, as today;
- a chart registry (`for_registry()`): what `credentials_from_authentication()` accepts (`token` with a literal
  `username`, or `cli`), returning `RegistryCredentials` or `None`.

A key in a store a plain build may not read raises `CredentialsNeedResolveError` (a `RemoteCredentialError`). For git it
fails the build as today; for a chart it leaves the chart unpinned (step 3). Any other credential problem (unknown or
disabled integration, wrong method, undeclared key) is a configuration error and fails the build,
`helm_registry_credentials_invalid`.

### The deploy: verify once, then install the archive

Before the first step, beside the target context check, `deploy run`, `plan` and `apply` go through every registry
chart of the Helm steps they will run:

1. No `chartDigest` in `meta.yaml`: `helm_chart_not_pinned`, an error naming the module and telling the user to build
   again where the chart can be pulled. This covers both an unpinned build and a build from before this change.
2. A cache entry for `chartVersion` that matches `chartDigest`: use it.
3. No entry: pull `chartVersion` exactly, with the deploy's registry credentials, into the cache, and compare.
4. A different digest: `helm_chart_digest_changed`. A failed pull: `helm_chart_pull_failed`. Both stop the deploy
   before any step runs.

Every Helm command for that module then uses the verified chart by path:

- the check phase lints the unpacked chart with the existing overlay (ADR-0043), so registry charts get lint;
- the dry-run and `helm upgrade` install the `.tgz`. No `repo add` or registry login happens at install time, and
  what is installed is the content that was verified.

`--smoke-test` contacts nothing and only reports that it would verify the charts. `deploy destroy` needs no chart
and is unchanged.

### SBOM

`HelmCollector`'s declared form reads each Helm module's `meta.yaml` in the build path:

- version: `chartVersion`, exact when pinned (`pkg:helm/authentik@2026.5.2`); `chartVersionConstraint` becomes the
  property `strata:version-constraint`;
- `hashes`: `SHA-256` from `chartDigest`;
- `chartAppVersion` as the property `strata:app-version`;
- `chartSubcharts` as components of their own (`pkg:helm/postgresql@15.5.38`), used by the chart;
- an unpinned chart keeps today's declared form, with the property `strata:pinned: false`.

The images a chart deploys (`helm template`) stay with step 3 of [sbom-generation.md](sbom-generation.md).

### Diagnostics

| Code                                | Severity | When                                                        |
| ----------------------------------- | -------- | ----------------------------------------------------------- |
| `helm_chart_not_pinned`             | warning  | build: the pull could not be attempted (no `helm`, credentials need `--resolve`, `--offline`) |
| `helm_chart_not_pinned`             | error    | deploy: `meta.yaml` has no `chartDigest`                    |
| `helm_chart_pull_failed`            | error    | build or deploy: the pull failed                            |
| `helm_chart_digest_changed`         | error    | build or deploy: the archive differs from the pinned digest |
| `helm_registry_credentials_invalid` | error    | existing code, now also at build                            |
| `helm_registry_auth_failed`         | error    | existing code, now also at build                            |
| `helm_unknown_configuration_key`    | warning  | existing code, now also for registry charts                 |
| `helm_lint_failed`                  | error    | existing code, now also for registry charts                 |

### What does not change

- Local charts: built, checked and installed as today.
- The deploy never re-resolves a version; it installs exactly `chartVersion`.
- No cluster is contacted at build (ADR-0054, condition 1).
- A machine without `helm` still builds; only a deploy of the unpinned chart fails.

## Implementation plan

Each phase ends with its tests passing, `ruff` and `mypy` clean.

- [x] **Phase 1 - the cache.** `integrations/helm_cache.py`: `ChartPin`, the entry layout, `archive_digest()`,
  `is_exact_version()`, `cached_pin()`, `pull_chart()` (helm pull to a temporary directory, unpack, read
  `Chart.yaml` and `charts/`, rename into place, digest conflict); `layout.helm_cache_dir()`. Unit tests with a fake
  `helm pull` that writes a real archive; one real `helm` test that packages a chart with `helm package` (subchart
  included), unpacks it into the cache, and reads its version, digest and subcharts.
- [x] **Phase 2 - credentials at build.** Split `RemoteCredentialResolver`; `for_registry()`; `CredentialsNeedResolveError`.
- [x] **Phase 3 - build.** Pin per registry module in `build_workload_modules()`; `meta.yaml` fields via
  `ResolvedModule.chart_pin`; key check; unpinned warning; `--dry-run` messages; `build run --offline`.
- [x] **Phase 4 - deploy.** Pre-flight verification in `deploy_run()`; `chart_archives` passed to
  `check_namespace()` / `deploy_namespace()`; lint, dry-run and install from the archive; drop the install-time
  `repo add` / `registry login`; `--smoke-test` message.
- [x] **Phase 5 - SBOM.** `HelmCollector` reads `meta.yaml`: exact version, hash, app version, subcharts, unpinned
  marker.
- [x] **Phase 6 - docs.** [helm-chart-deployment.md](../design/helm-chart-deployment.md),
  [sbom-generation.md](../design/sbom-generation.md), [sbom-generation.md](sbom-generation.md) step 3, build command
  docs (`--offline`, `helm` for registry charts), the changelog, gap 8 in [helm-gap-analysis.md](helm-gap-analysis.md).

## Related Decisions

- [ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md) - what the build may fetch, and the
  unpinned rule
- [ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md) - the lint check phase, now covering
  registry charts
- [ADR-0042](../decisions/0042-private-chart-registries-authenticate-through-the-remote-integration.md) - registry
  credentials
- [ADR-0019](../decisions/0019-version-pinning.md) - version pins, which set `chart_version`

## Remaining Work / Open Questions

- **haven's credentials.** Which of haven's 6 registries need credentials, and in which store? A vault-held password
  leaves haven's plain build unpinned until it passes `--resolve`.
- **haven's charts under lint.** A third-party chart may fail `helm lint` with haven's values for reasons haven
  cannot fix. If one does, lint for registry charts may need to be a warning.
- **Failing at build instead of deploy.** A CI build job may want an unpinned chart to fail the build
  (`--require-pinned`, or a solution setting). Left out until asked; the deploy refusal already guarantees safety.
- **Cache cleanup.** `.strata/helm/` grows with every version built. `.strata/remotes/` has the same property and no
  cleanup; a shared `strata cache prune` is out of scope here.
- **strata's image.** [Dockerfile.cli](../../Dockerfile.cli) ships no `helm`, so a CI build in it leaves every
  uncached registry chart unpinned.

## Changelog

- 2026-11-07: Created as a check-phase pull.
- 2026-11-07: Rewritten under ADR-0054: the build pulls into `.strata/helm/`, records the version and digest, and the
  deploy verifies and installs that archive.
- 2026-11-07: The unpinned rule (ADR-0054 clarified): a pull the machine cannot attempt warns and leaves the chart
  unpinned; the deploy refuses it. Split into phases.
- 2026-11-07: Implemented, phases 1-6. Deviations: the deploy-side "not pinned" error also covers a `meta.yaml` with a
  digest but no version; the test suite closes `can_pull_charts()` by default (`tests/conftest.py`, marker
  `chart_pulls`) so no test pulls just because the machine has `helm`; the registry-chart tests of the old install-time
  path were rewritten against `pull_registry_chart()`.
- 2026-11-10: Staging cleanup is best effort (`ignore_cleanup_errors`), and a failed store is a `ChartPullError`. A
  Windows file lock on the new archive made `test_deploy_run_stops_when_the_registry_serves_a_different_archive` fail
  now and then in the full suite, with a `PermissionError` in place of `helm_chart_digest_changed`.
