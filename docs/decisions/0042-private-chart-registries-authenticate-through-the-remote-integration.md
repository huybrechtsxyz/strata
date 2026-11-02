# Private chart registries authenticate through the remote's integration

- Status: accepted
- Date: 2026-10-21
- Related: [docs/design/helm-chart-deployment.md](../design/helm-chart-deployment.md) (how it works),
  [docs/integrations/chart-registries.md](../integrations/chart-registries.md) (user guide),
  [ADR-0021](0021-integration-layer.md) (the integration layer and its `sources` capability)

## Context and Problem Statement

A module can pull a Helm chart from a registry that needs a login: an HTTP chart repository (ProGet, Nexus, Artifactory) or an
OCI registry (ACR, a private GHCR repository). Until now strata fetched every chart anonymously, and v1 never supported a login
either. The schema already had a hook: `SolutionRemoteModel.integration` is documented as "credentials for this remote", and
`Capability.SOURCES` is described as "git/oci/helm auth". But only git used it, so a config that named an integration on a
`helm`/`oci` remote passed `strata validate` and then failed at deploy with a 401.

Three things make this more than wiring. A password must never be visible on a command line. Helm persists what it is given
(`helm repo add --password-stdin` writes the password in plain text to `repositories.yaml`, and credentials only work in the
configuration they were added to). And `build run` never pulls a chart, so the secret is needed at deploy time, when most
values are already resolved.

## Considered Options

- **Use the remote's integration** (`capabilities: [sources]`, `method: token` with a literal `username` and a key naming a
  secret), resolve it at deploy time, pass the password on stdin, and isolate Helm's state per credentialed module. Chosen.
- **Credentials inline on the remote or the module.** Rejected: `Integration` is strata's single credential mechanism, and
  the model already rejects a password embedded in a URL.
- **Leave it to the operator** (`helm registry login` or `repo add --username` before the deploy, using `method: cli`). Kept as
  the ambient fallback, but not the answer: it is not declarative, not per deployment, and invisible to `strata validate`.
- **Pass the password with `--password` or an environment variable.** Rejected: argv is visible in `ps` and Task Manager, which
  is the leak class `run_command()` already forbids.
- **Log in to the user's own Helm configuration and clean up afterwards** (`helm repo remove`, `helm registry logout`).
  Rejected: a crash leaves the secret behind, and it edits a configuration strata does not own.
- **Reuse the build-time git credential resolver** (`RemoteCredentialResolver`). Rejected: it exists to read a few keys during a
  plain build without network access, and it is git-specific; deploy has already resolved every declared value.

## Decision Outcome

Chosen: a private chart registry is a `helm` or `oci` remote that names an integration, and deploy authenticates with it.

- Only `method: token` (or ambient `cli`) can authenticate a chart registry, and `username` is required: it is a literal, not a
  key, and unlike a git token it cannot be omitted.
- Credentials are read at deploy time from the values `deploy_run()` already resolved, only for the registries a step's modules
  pull charts from. A remote nothing deploys from is never looked at.
- The password reaches Helm only on stdin, and never appears in argv, the environment, output or an error message.
- Each credentialed module runs its login, its preview and its upgrade in one temporary Helm configuration, removed afterwards;
  the user's own configuration is untouched.
- A rejected login is an error for that module (without credentials a chart cannot be fetched at all), unlike an anonymous
  repository whose refresh failure is only a warning.
- For a `helm`/`oci` remote, `strata validate` reports a mis-declared integration as an error, because nothing else uses a chart
  registry's integration. For a git remote the same findings stay warnings, because GitOps and audit pushes also use it.

## Consequences

- Good: a private registry is declared once, reviewed like any other integration, and fails at `strata validate` or at the start of
  the step rather than as a 401 mid-deploy.
- Good: no secret value is ever on a command line, in a log, or left in a configuration file after the deploy.
- Bad: a credentialed module re-downloads its repository index on every deploy, since its configuration is thrown away.
- Bad: managed-identity authentication (an ACR access token from `az acr login`) is not supported; the workarounds are `method: cli`
  after logging in yourself, or putting a token in a secret.
- Neutral: an inline `chart_repository` has no integration to name, so a private registry must be a named remote.
