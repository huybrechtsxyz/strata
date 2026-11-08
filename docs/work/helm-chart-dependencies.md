# Helm local chart dependencies fetched at build - Work

- Status: in-progress (implemented; open questions remain)
- Last updated: 2026-11-08

## Overview

Gap 9 of [helm-gap-analysis.md](helm-gap-analysis.md), designed under
[ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md) and reusing the chart cache of
[helm-registry-chart-checks.md](helm-registry-chart-checks.md) (gap 8).

A local chart (`source_path`) may declare `dependencies:` in `Chart.yaml`. Helm installs only what is in the chart's
`charts/` directory, and strata never fills it: `sync_module_source()` copies the chart directory as it is. Unless
the dependencies are committed under `charts/`, `deploy run` fails at `helm upgrade` with "found in Chart.yaml, but
missing in charts/ directory", after earlier steps have applied.

The fix: `build run` fills the build copy's `charts/` with each dependency at an exact version, from the
`.strata/helm/` cache that gap 8 introduced, and records what it placed in `meta.yaml`. The deploy installs the build
directory as it does today, so it needs no network for dependencies. A dependency the build could not fetch leaves the
chart unpinned, and the deploy refuses it, as for registry charts.

## Current state

Verified on 2026-11-08 against the source and with Helm 3.13.2 on a test chart served from a local HTTP repository.

| Case                                                            | What happens today                                                                 |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Dependencies committed under `charts/`                          | Works                                                                              |
| Dependencies not committed                                      | `helm lint` passes (it only warns); `helm upgrade` fails at deploy                 |
| `repository: file://../common`                                  | Fails even after `helm dependency build`: the chart is copied alone into `build/`, so `../common` is gone |
| Configuration for a subchart (`configuration: {sepv: {...}}`)   | Warned as `helm_unknown_configuration_key`: the key check only reads the parent's `values.yaml` |
| SBOM                                                            | The declared `dependencies` (a range stays a range); nothing about what is installed |

What Helm itself does, from the test:

- `helm dependency build` without a `Chart.lock` behaves like `helm dependency update` and writes a new
  `Chart.lock`. Running it would invent a tool's lock, which ADR-0054 condition 4 rules out.
- With a `Chart.lock`, `helm dependency build` fails for an HTTP repository that is not in the Helm configuration
  ("no repository definition ... helm repo add").
- An out-of-sync `Chart.lock` fails with a clear message.
- Archives that something else placed in `charts/` work without any lock. `helm template` does not check their
  versions, but `helm dependency list` does, offline: `ok`, `wrong version` or `missing` per dependency.
- A value under the dependency's name in the parent's values (`sub: {replicaCount: 3}`) reaches the subchart.

How OMP charts declare dependencies (GitHub search of `org:OMP`, 2026-11-08): about 18 charts declare
`dependencies`, 2 commit a `Chart.lock`, and 1 uses `file://`. `SP_BaseSolution` is typical: a wrapper chart with one
exact-version dependency (`sepv` 99.99.343) from an HTTP repository and no lock. `platform-app-of-apps` commits its
subcharts under `charts/`. Requiring a `Chart.lock` would reject the common case. haven's charts could not be
checked: its fixture in `.v2-haven/` is empty.

## Design

### Which version

Strata resolves each dependency itself instead of running `helm dependency build`, so it never writes a `Chart.lock`
and can use the gap 8 cache.

| Dependency in `Chart.yaml`                                   | What the build places in `charts/`                                                            |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Already in the source's `charts/` (archive or directory)     | Nothing; it is recorded as committed                                                          |
| Listed in a committed `Chart.lock`                           | The lock's exact version                                                                      |
| Not in the lock while a lock exists                          | Error `helm_chart_dependency_lock_out_of_sync`: run `helm dependency update` and commit it    |
| No lock, exact version                                       | That version                                                                                  |
| No lock, a range                                             | The newest matching version, resolved by `helm pull --version` and recorded in `meta.yaml`, as gap 8 does for a registry chart with a range |
| `repository: file://<path>`                                  | The directory, copied unpacked to `charts/<name>/`; no Helm or network needed                 |
| No `repository` and not in `charts/`                         | Error `helm_chart_dependency_missing`                                                         |
| `repository: "@name"` or `alias:name` (a Helm repository name) | Error `helm_chart_dependency_repository_name`: the name depends on the machine's Helm configuration; use the URL |

Recording a resolved range in `meta.yaml` is strata's own record, not a tool's lock, so condition 4 holds. It is
also what gap 8 already does. The source tree is never written to: everything lands in the build copy.

A `file://` path is resolved against the chart's origin (the solution or the remote's checkout) and must stay inside
that repository root (`resolve_within()`), like `source_path`. The copied chart's own dependencies go through the same
table, so nested `file://` charts work.

### Fetching

- **HTTP(S) and OCI** dependencies are pulled with `helm_cache.pull_chart()` into
  `.strata/helm/<source>/<name>/<version>/`, where `<source>` is `_repo_alias(url)`. A dependency and a registry chart
  of the same chart, version and repository share one cache entry. The archive is copied into the build copy's
  `charts/`.
- **Exact versions** (exact, locked, or once a range is resolved) are served from the cache without `helm` or the
  network.
- **Credentials:** a dependency whose repository URL matches a declared remote's URL authenticates through that remote
  (`RemoteCredentialResolver.for_registry()`, gap 8). Any other repository is pulled anonymously. Every pull runs in
  the isolated Helm configuration, so the user's own `repositories.yaml` is neither read nor written.
- **Post-check:** when `helm` is installed, `helm dependency list <build copy>` runs once, offline. Any status other
  than `ok` is an error (`helm_chart_dependency_wrong_version`, `helm_chart_dependency_missing`). This also catches a
  committed `Chart.lock` whose version does not satisfy `Chart.yaml`, without strata parsing semver ranges.

### Unpinned

As in ADR-0054's unpinned rule: a fetch the machine cannot attempt is a warning, not an error. That covers no `helm`
on a cache miss, credentials that need `--resolve`, and `--offline` on a cache miss. Committed and `file://`
dependencies are never unpinned: they need neither `helm` nor the network. A failed fetch (the version does not
exist, authentication rejected) is an error.

The warning is `helm_chart_dependency_not_pinned`: "Namespace 'x', module 'y': dependency 'sepv' 99.99.343 is not
pinned: 'helm' is not installed. A deploy of this build stops at this chart; build again where the dependency can be
pulled."

### What meta.yaml records

For a local chart with dependencies:

```yaml
chartDependencies:
  - name: sepv
    version: 99.99.343            # exact
    versionConstraint: "~99.99"   # only when Chart.yaml declared something else
    repository: https://cdn-feeds.omp.com/helm/custom-helm/
    digest: sha256:...            # absent for file:// (a directory, not an archive)
    origin: pulled                # pulled | cache | committed | file
chartDependenciesUnpinned: [sepv] # only when the build left some unpinned
```

### Deploy

`deploy run` keeps installing the build directory, which now holds `charts/`. Its pre-flight (the same place as
`_verify_registry_charts()`) refuses a module whose `meta.yaml` has `chartDependenciesUnpinned`, before the first
step: "Namespace 'x', module 'y': dependency 'sepv' was not pinned by the build, so the chart cannot be deployed.
Build again where the dependency can be pulled (helm installed, its registry credentials readable, without --offline)."

Unlike registry charts, the deploy does not verify digests: the archives are part of the build output, which the
deploy already trusts as a whole.

### Configuration key check

`check_configuration_keys()` also accepts as known every dependency's name or `alias`, and `global`. Their children
are not checked: the subchart's own `values.yaml` would be needed, and nested checking is one level deep today
anyway.

### SBOM

`HelmCollector` reads `chartDependencies` from `meta.yaml` for a local chart. Each dependency becomes a component at
its exact version, with `hashes` from the digest, `strata:subchart-of: <chart>@<version>`, and
`strata:version-constraint` when the declaration was a range. This replaces today's declared-form entry for the same
dependency. An unpinned dependency keeps its declared form with `strata:pinned: false`.

### Diagnostics

| Code                                        | Severity | When                                                                  |
| ------------------------------------------- | -------- | --------------------------------------------------------------------- |
| `helm_chart_dependency_not_pinned`          | warning  | build: the machine cannot attempt the fetch                           |
| `helm_chart_dependency_lock_out_of_sync`    | error    | build: a committed `Chart.lock` lacks a declared dependency            |
| `helm_chart_dependency_missing`             | error    | build: no repository and not committed, or `dependency list` says `missing` |
| `helm_chart_dependency_wrong_version`       | error    | build: `dependency list` says `wrong version`                          |
| `helm_chart_dependency_repository_name`     | error    | build: `@name` or `alias:name` repository                              |
| `helm_chart_dependency_outside_source`      | error    | build: a `file://` path leaves the repository root                     |
| `helm_pull_failed`, `helm_registry_auth_failed` | error | build: as for registry charts (gap 8)                                 |
| `helm_chart_not_pinned`                     | error    | deploy: `chartDependenciesUnpinned` is set (same code as gap 8)       |

## Rollout and risk

- A chart that commits its dependencies under `charts/` behaves exactly as before.
- A chart that deploys today with dependencies missing does not exist: `helm upgrade` fails on it. Nothing that works
  today breaks.
- New: `build run` needs `helm` and the network for a local chart with uncommitted remote dependencies, unless the
  cache already has them. Without them it warns, and the deploy refuses. Today that deploy fails anyway, later.
- The key check stops warning about subchart keys: fewer warnings, never more.

## Related Decisions

- [ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md) - what `build run` may fetch, and the
  unpinned rule
- [helm-registry-chart-checks.md](helm-registry-chart-checks.md) - the chart cache, `pull_chart()`, `for_registry()`
- [ADR-0042](../decisions/0042-private-chart-registries-authenticate-through-the-remote-integration.md) - registry
  credentials through the remote's integration
- [helm-chart-deployment.md](../design/helm-chart-deployment.md) - how Helm works today

## Remaining Work / Open Questions

### Implementation plan

- [x] **Phase 1 - plan.** A pure function from `Chart.yaml`, an optional `Chart.lock` and the source's `charts/` to
  one entry per dependency: committed, locked, exact, range, file, or one of the errors. Tests per table row.
- [x] **Phase 2 - fetch.** Pull into the cache and copy into `charts/`; copy `file://` directories with the root
  check and recursion; credentials by repository URL; the unpinned rule; `helm dependency list` post-check;
  `meta.yaml` fields. Tests with the fake helm from gap 8, plus one real-helm test on the local HTTP repository.
- [x] **Phase 3 - deploy and key check.** The pre-flight refusal; dependency names, aliases and `global` known to
  the key check.
- [x] **Phase 4 - SBOM and docs.** `HelmCollector` reads `chartDependencies`; helm-chart-deployment.md,
  sbom-generation.md, chart-registries.md, CHANGELOG, gap analysis.

### Decided

- **Credentials by URL** (2026-11-08): a dependency whose repository URL matches a declared helm or oci remote's URL
  (trailing `/` ignored) uses that remote's credentials; any other is pulled anonymously.
- **Range without a lock** (2026-11-08): resolved at every build without a warning, as gap 8 does; `meta.yaml` and the
  SBOM record what was placed.

### Open questions

1. **haven.** Its charts could not be checked here: the fixture in `.v2-haven/` is empty. A `build run` of haven would
   show whether any of its local charts has dependencies it does not commit.
2. **Dry run.** `build run --dry-run` does not materialise local charts, so it reports nothing about their
   dependencies. Planning from the source without copying would need the remote's checkout, which a dry run may not
   clone. Left as is until someone needs it.

## Changelog

- 2026-11-08: Implemented, phases 1-4. Deviations from the design above:
  - The plan reads the source chart, not the build copy: a copy kept by `--no-clean` holds what an earlier build
    placed, which would otherwise be recorded as committed.
  - Nested `file://` dependencies are recorded in the same flat `chartDependencies` list as the chart's own.
  - `requirements.yaml` and `requirements.lock` (`apiVersion: v1` charts) are read too.
  - `sync_module_source()` now returns the repository root and the copied origin, which `file://` resolution needs.
  - Fixed a flaky gap 8 test fixture: the fake `helm pull` wrote gzip archives stamped with the current second, so
    a build pull and a deploy pull a second apart had different digests (`helm_chart_digest_changed`). The fixtures
    now write a fixed gzip timestamp. strata itself was right to reject them.
- 2026-11-08: Created. Verified Helm 3.13.2's dependency behaviour on a test chart, and how OMP charts declare
  dependencies. Corrected the gap analysis: `helm lint` only warns about missing dependencies; the failure is at
  install.
