# Git (`capabilities: [sources]`)

Lets `strata build run` clone a **private** git repository: a manifest `remotes:` entry in
`strata.yaml`, or an asset's own `spec.location`. Without an integration, a clone uses whatever
credentials git already has (an SSH agent, a credential helper); that stays the right choice on a
developer machine, and for CI that checks the repository out itself (`fetch: external`).

## Prerequisites

- A token or SSH key with read access to the repository.
- That value held where a build can read it: an environment variable (a CI secret mapped to one,
  or `--env-file` locally), or, with `build run --resolve`, an integration-backed store such as
  Key Vault.

## Configuring the `kind: integration` document

`spec.authentication` names **keys** of `secrets` declared in an
[`environment`](../config/environment.md) the deployment reaches. The values themselves never
appear in a document.

A token:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: git-customers
spec:
  type: git
  capabilities: [sources]
  authentication:
    method: token
    token:
      token: CUSTOMER_GIT_TOKEN   # the key of a secret
      username: build-bot         # optional; the token answers git's password prompt
```

An SSH key:

```yaml
spec:
  type: git
  capabilities: [sources]
  authentication:
    method: ssh_key
    ssh_key:
      private_key: GIT_SSH_KEY        # keys of secrets
      passphrase: GIT_SSH_PASSPHRASE  # optional
      known_hosts: GIT_KNOWN_HOSTS    # optional, but if set it must resolve (no trust-on-first-use)
```

The secrets, in the environment:

```yaml
spec:
  secrets:
    - key: CUSTOMER_GIT_TOKEN
      store: environment
      value: CUSTOMER_REPOS_PAT   # the build job exports this variable from its CI secret
```

`method: cli` (or no `authentication`) means "whatever git already has". Other methods, such as
`oauth2`, cannot authenticate a git fetch and are an error.

## Using it

```yaml
# strata.yaml
spec:
  remotes:
    - name: infra
      type: git
      url: https://git.example.com/org/infra.git
      reference: v2.1.0
      integration: git-customers
```

```yaml
# an asset
spec:
  location:
    url: https://git.example.com/customers/c0062-api.git
    reference: v1.4.0
    integration: git-customers
```

One integration serves any number of remotes and assets. A customer with a distinct identity gets
an integration document of its own.

## What the build does

- The credential is read **only when a clone is about to run**. `--dry-run`, an existing checkout,
  `fetch: external` and a remote without an `integration` read nothing.
- Without `--resolve`, the secret must use a `constant` or `environment` store. A token held in an
  integration-backed store fails with a message naming the key and its store; run
  `strata build run --resolve` to allow it.
- It is resolved once per build, however many repositories use the integration.
- The value reaches git only through its process environment (a temporary `GIT_ASKPASS` helper, or
  an SSH key file with `0600` permissions) and is removed afterwards. It is never on a command line,
  in the build output, or in an error message: a failed clone's text has the value replaced by `***`.

## Validation

`strata validate` checks, before any build:

| Code                                  | Severity | When                                                                                         |
| ------------------------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `remote_integration_unknown`          | error    | a manifest remote names an integration that does not exist                                   |
| `remote_integration_not_sources`      | error    | a manifest remote's integration does not declare `sources`                                   |
| `asset_integration_not_sources`       | error    | an asset `location`'s integration does not declare `sources`                                 |
| `asset_integration_disabled`          | error    | an asset `location` with `fetch: strata` names a disabled integration                        |
| `asset_integration_auth_unsupported`  | error    | an asset `location` with `fetch: strata` names an integration using `oauth2` or similar      |
| `fetch_credential_key_undeclared`     | error    | a deployment clones an asset with an integration whose key none of its environments declares |
| `remote_integration_disabled`         | warning  | a manifest remote's integration is disabled                                                  |
| `remote_integration_auth_unsupported` | warning  | a manifest remote's integration uses a method git cannot use                                 |
| `remote_integration_ignored`          | warning  | an asset `location` sets `integration` with `fetch: external`                                |

The manifest remote findings are warnings because GitOps and audit pushes also use a remote's
integration and ignore `enabled`. (For a `helm` or `oci` remote the same findings are errors; see
[chart-registries.md](chart-registries.md).) `fetch_credential_key_undeclared` looks at the assets a
deployment's workspace steps select (bound through the deployment), whether they are reached by
`location` or by a manifest remote, so a missing secret in one customer's environment is found by
`strata validate` instead of by that customer's pipeline. Whether the key's store is readable
without `--resolve` is only known at build time.

A missing integration on an asset `location` is the generic `unknown_reference` error. A `url` with a
password (`https://user:token@host/...`) is rejected, and so is an http(s) username that looks like
a token (a `ghp_`/`github_pat_`/`glpat-` prefix, or 32 or more token characters, the shape of a
personal access token pasted as `https://<token>@host/...`). `git@host:org/repo.git`,
`ssh://git@host/...` and an ordinary `user@`, such as Azure DevOps' `https://myorg@dev.azure.com/...`,
are fine.

At build time, an undeclared key or an unset environment variable fails the build, naming the asset
or remote and the key, before any `git` call.
