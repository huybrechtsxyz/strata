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
  - `sinks[]` — send the audit record to a git repository or an integration. A git sink names a `remote`, the
    `branch` to push to, a folder `path`, and an `action` (`push`, the default; `commit`; `none`; see below). It
    receives `_manifest.json`, `_metrics.json` when it admits `deployment.measured`, the build's `resolved.yaml`
    and `sbom.json`, and `_handover.md` when a deploy wrote it;
  - `require_remote_commits` — fail `deploy run` when the build does not record a clean commit for every
    repository it took files from (default `false`; see below)
- `spec.security` — platform-wide rules for what environment and workspace documents may declare, all optional:
  - `allowed_variable_stores`, `allowed_secret_stores`, `allowed_feature_stores`, `allowed_key_value_stores` —
    closed lists of the store types allowed; omit a list for no restriction;
  - `disallowed_variable_keys` — key patterns a variable or feature may not use (see below).

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

## Keys a variable may not use

A secret declared as a variable loses a secret's protection: its value is written in clear to
`strata.auto.tfvars.json` and Terraform state, and with `store: constant` it sits in your git repository.
`disallowed_variable_keys` lets the platform name the keys that give such a mistake away:

```yaml
spec:
  security:
    disallowed_variable_keys:
      patterns: ["*PASSWORD*", "*PASSWD*", "*PWD*", "*SECRET*", "*TOKEN*", "*API_KEY*", "*PRIVATE_KEY*",
                 "*CONNECTION_STRING*", "*CREDENTIAL*"]
      exceptions: ["*_SECRET_ID", "*_SECRET_NAME", "*_SECRET_URI", "*_TOKEN_URL"]
      enforcement: warn
```

- `patterns` (required, at least one) — globs (`*`, `?`, `[...]`) matched against the whole key, ignoring case.
  Strata has no list of its own; the one above is a starting point. Avoid a bare `*KEY*`: it would match
  `KEY_VAULT_NAME` and `PARTITION_KEY`. [Catch secrets declared as variables](../how-to/catch-secrets-declared-as-variables.md)
  gives a fuller list, the patterns to avoid, and how to roll it out from `warn` to `deny`.
- `exceptions` — keys never reported, even when a pattern matches: typically a key that names or points at a
  secret in a vault instead of holding it.
- `enforcement` — `warn` (default) reports the key; `deny` fails `strata validate`, and with it `build run` and
  `deploy run`.

`strata validate` checks every `spec.variables[].key` and `spec.features[].key` in every environment document,
whatever its store, and reports `variable_key_looks_secret`. The fix is to declare the key under `spec.secrets`,
or, when it is not a secret, to add an exception here. An exception that matches no variable or feature key
in any environment document is reported as information (`disallowed_variable_keys_unused_exception`). Without
the block, no key is checked.

## Which commits were deployed

`build run` writes two keys in `build/<deployment>/resolved.yaml`. The solution's own commit sits under `solution`.
Every git remote the build copied files from sits under `remotes`, and so does every document remote
(`spec.discovery.remotes` in [solution.md](solution.md#documents-in-other-repositories)). An asset's own
`spec.location` is listed as `asset:<name>`.

```yaml
solution: {ref: v2.3.0, commit: 5d0e7a9c31..., dirty: false}
remotes:
  iac: {url: https://github.com/org/iac.git, ref: v1.4.0, commit: 3f9c2e1d4b..., dirty: false}
```

- `ref` is the reference after version pins for a remote with a `reference`. For a `path` remote and for the
  solution, the pipeline chose it, so strata reads it from the checkout: the tag HEAD is exactly on (the highest
  version when there are several), else the branch, else `null` (a checkout at a bare commit). The checkout must
  have the tags: in Azure DevOps, set `fetchTags: true` on the `checkout` step.
- `dirty: true` means files the build took had uncommitted changes, so the commit alone does not describe them.
  Only the paths the build copied are checked. Files that git ignores do not count.
- The solution's `commit` is the one the SBOM records: a CI commit variable first, otherwise `git rev-parse HEAD`.

The deploy's audit manifest copies the record. `artifacts.repositories.<name>` gains `commit` and `dirty`, and the
solution's record becomes `commit_sha`, `commit_dirty` and `commit_ref`. So "which version ran" is answered from the
audit trail alone, and the manifest names what was built, even when
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

## Audit files in a repository your pipeline checked out

A git audit sink usually names a remote with a `reference`. Strata then keeps its own clone under
`.strata/audit-push/`, fetches and resets it before each write, and pushes with the remote's `integration`.

When the pipeline has already checked the repository out, name a remote with a `path` instead
(see [solution.md](solution.md#a-repository-your-pipeline-checked-out)). Strata then writes in that checkout:

```yaml
# strata.yaml
remotes:
  - name: inventory
    type: git
    url: https://dev.azure.com/org/project/_git/inventory
    path: repos/inventory          # the pipeline's checkout

# configuration.yaml
spec:
  audit:
    sinks:
      - name: inventory
        required: true
        git:
          remote: inventory
          branch: main             # the branch pushed to
          path: deploys
          action: push             # the default
```

- **What strata does in the checkout.** It never clones, resets or switches branch in it. It writes the audit
  files under `path` and commits only those files. Other changes in the checkout stay as they were: modified,
  staged or untracked. The commit author is the deploying actor. Strata passes it per command and does not
  change the checkout's git config.
- **Pushing.** The push runs `git push origin HEAD:<branch>`, so a detached checkout works. It uses the
  checkout's own credentials. In Azure DevOps that means `persistCredentials: true` on the `checkout:` step. When
  the branch moved since the checkout, strata fetches it, rebases the audit commit onto it and pushes again, three
  times in all.
  - **Check out `branch`** (or a commit already on it). Strata pushes only commits it made: when the checkout holds
    others the branch lacks, such as a feature branch or a pull request's merge, the push is refused with a message
    naming the commit, so they never land on `branch`.
  - A commit an earlier run left unpushed, on an agent that keeps its checkout, is pushed by the next run.
  - A push the server refuses, such as a protected branch, fails at once without a rebase.
  - `--autostash` keeps the checkout's other changes, but a staged change comes back unstaged.
  - A rebase that conflicts is aborted. Each deploy writes its own folder, so a conflict means two deploys wrote the
    same audit path.
- **`action`** says how far strata goes. Each step includes the ones before it:

  | `action`         | Strata                     | Then                                      |
  | ---------------- | -------------------------- | ----------------------------------------- |
  | `push` (default) | writes, commits and pushes | -                                         |
  | `commit`         | writes and commits         | a later pipeline step pushes              |
  | `none`           | writes the files only      | a later pipeline step commits and pushes  |

  With `commit` or `none` the deploy reports `audit_sink_not_pushed` as information. Both need a remote with a
  `path`. With a `reference`, strata writes into its own clone, which nothing else reads, so `strata validate`
  reports `audit_sink_action_needs_path_remote`.
- **`required`** keeps its meaning. With `required: true`, a failed write, commit or push fails the deploy
  (`audit_sink_push_failed`). Without it, the failure is a warning.
- **Reading and writing the same repository.** The build records the checkout's commit before the deploy commits
  the audit files, so the record still names what was built. But a sink `path` inside a `source_path` the build
  takes from the same remote would put audit files into the next build. `strata validate` warns
  (`audit_sink_overlaps_source`).
- **Reading the records back.** `strata audit status` and `strata audit changes` do not read the pipeline's
  checkout, which may be on another branch or missing on your machine. They clone the remote's `url` at the
  sink's `branch` into `.strata/audit-read/`, as for a remote with a `reference`, even though a `path` remote is
  otherwise `fetch: external`. With `commit` or `none`, they show a record only after the pipeline has pushed
  it; until the branch exists they report `audit_read_branch_missing` rather than another branch's records.
- **Credentials for reading.** Without an `integration` on the remote, the read uses your own git credentials.
  With one, name a deployment (`strata audit status <deployment>`): its environments resolve the integration's
  token or SSH key, as a deploy does for the push. In a pipeline that matters, because the pipeline's
  credentials usually live only in its own checkout (`persistCredentials`), which the read does not use. Without
  a deployment the read falls back to ambient credentials, and a failure says how to use the integration.

A GitOps step can write to a `path` remote the same way, with its own `branch` and `action`; see
[solution.md](solution.md#a-repository-your-pipeline-checked-out).

## Notes

- `providers`/`topologies` are plain document names (ADR-0015), resolved by discovery — not
  embedded registry entries. Each provider/topology type gets its own `ProviderConfig`/
  `TopologyConfig` document (see [providerconfig.md](providerconfig.md)/[topologyconfig.md](topologyconfig.md)),
  so adding a new type is a new file with its own PR, not an edit to a shared list.
- `spec.remotes` is **not** here — remotes live on the solution manifest (`strata.yaml`, see
  [solution.md](solution.md)), since Configuration itself can live in a remote and remotes must
  resolve first.
