# Configuration (`kind: configuration`)

Platform-wide policy: which provider types and topology types this solution allows. A
deliberately small slice of v1's `configuration_model.py` — security policy, path conventions,
logging, cost/drift tracking are not ported (only added when a v2 feature actually needs them).

## Schema

- `spec.providers[]` — names of `ProviderConfig` documents (the provider type registry)
- `spec.topologies[]` — names of `TopologyConfig` documents (the topology type registry)
- `spec.additional_topologies` — allow topology types not listed in `spec.topologies` (default `false`)
- `spec.audit` — the deployment audit trail, all optional:
  - `event_overrides` — switch event types on or off;
  - `sinks[]` — send the audit record to a git repository or an integration;
  - `require_remote_commits` — fail `deploy run` when the build does not record a clean commit for every
    repository it took files from (default `false`; see below)

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: example-config
spec:
  providers:
    - azure
  additional_topologies: false
  topologies:
    - kubernetes
```

## Which commits were deployed

`build run` writes two keys in `build/<deployment>/resolved.yaml`. The solution's own commit sits under `solution`.
Every git remote the build copied files from sits under `remotes`. An asset's own `spec.location` is listed as
`asset:<name>`.

```yaml
solution: {commit: 5d0e7a9c31..., dirty: false}
remotes:
  iac: {url: https://github.com/org/iac.git, ref: null, commit: 3f9c2e1d4b..., dirty: false}
```

- `ref` is the reference after version pins. It is `null` for a `path` remote, because the pipeline chose it.
- `dirty: true` means files the build took had uncommitted changes, so the commit alone does not describe them.
  Only the paths the build copied are checked. Files that git ignores do not count.
- The solution's `commit` is the one the SBOM records: a CI commit variable first, otherwise `git rev-parse HEAD`.

The deploy's audit manifest copies the record. `artifacts.repositories.<name>` gains `commit` and `dirty`, and the
solution's record becomes `commit_sha` and `commit_dirty`. The manifest therefore names what was built, even when
build and deploy run in separate jobs or a checkout moved in between. A build made by an older strata has no record.
Its deploy reads the commits at deploy time, as before.

The build warns about a gap: `audit_remote_commit_unavailable` when a commit could not be read, and
`remote_checkout_dirty` when files had uncommitted changes. A solution that is not a git repository records `null`
without a warning.

To make a gap fail the deploy, turn the option on:

```yaml
spec:
  audit:
    require_remote_commits: true
```

`deploy run`, `plan` and `apply` then stop before anything changes, with one error per gap:

| Error                              | Cause                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------ |
| `audit_remote_commit_unavailable`  | The solution or a remote has no recorded commit, for example a plain copy or a solution outside git |
| `remote_checkout_dirty`            | Files the build took had uncommitted changes, or could not be checked    |
| `build_remote_provenance_missing`  | The build was made by a strata that did not record commits               |

A build still only warns, so building an uncommitted edit on a workstation keeps working. Rebuild from a clean,
committed checkout to deploy. A destroy is not checked. A recorded commit may never have been pushed. Strata does not
check that, because a pipeline's checkout usually has no remote branches to compare with.

- `providers`/`topologies` are plain document names (ADR-0015), resolved by discovery — not
  embedded registry entries. Each provider/topology type gets its own `ProviderConfig`/
  `TopologyConfig` document (see [providerconfig.md](providerconfig.md)/[topologyconfig.md](topologyconfig.md)),
  so adding a new type is a new file with its own PR, not an edit to a shared list.
- `spec.remotes` is **not** here — remotes live on the solution manifest (`strata.yaml`, see
  [solution.md](solution.md)), since Configuration itself can live in a remote and remotes must
  resolve first.
