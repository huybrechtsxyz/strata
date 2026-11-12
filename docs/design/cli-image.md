# strata container image - Design

- Status: implemented
- Last updated: 2026-11-12

## Overview

`Dockerfile.cli` builds strata's container image in two variants, published to `ghcr.io/huybrechtsxyz/strata` (and
to Docker Hub when the release workflow has registry secrets):

| Variant | Target | Tags | Holds |
| --- | --- | --- | --- |
| **slim** | `slim` (also the default without `--target`) | `<v>`, `<major>.<minor>`, `<v>-slim`, `<major>.<minor>-slim`, `latest`, `latest-slim` | strata and the tools strata itself calls |
| **full** | `full` | `<v>-full`, `<major>.<minor>-full`, `latest-full` | slim, plus Helm 4, the Docker CLI with Compose, Trivy and Checkov |

A pre-release tag (`2.7.0-rc1`) never moves `latest` or `latest-<variant>`. `ci-image.yml` builds a branch image on
demand: `<branch>` and `<branch>-slim`, and `<branch>-full`.

```bash
docker run --rm -v "$PWD:/work" -w /work ghcr.io/huybrechtsxyz/strata:2.7.0 validate config
docker run --rm -v "$PWD:/work" -w /work ghcr.io/huybrechtsxyz/strata:2.7.0-full build run prd-deployment
```

The bare tag is slim, so a pipeline that pulled `strata:<v>` before the variants existed gets the same image.

## Current Design

### What goes in which variant

The rule: **slim holds strata and the tools strata itself calls; full adds the tools a pipeline runs strata with,
where their licence lets a public image carry them.**

| Tool | slim | full | Why |
| --- | --- | --- | --- |
| Python 3.13, strata | yes | yes | installed from `uv.lock` with `--locked`, the versions CI tested |
| `git` | yes | yes | strata calls it: remotes at build, the commit SHA in the audit record and the image gate, audit push, GitOps |
| `openssh-client` | yes | yes | `auth.method: ssh_key` runs git over ssh; the host key comes from `ssh_key.known_hosts` |
| Helm 4 | no | yes | Helm steps, and `build run` itself: registry chart pinning, chart dependencies, SBOM rendering |
| Docker CLI + Compose plugin | no | yes | Compose steps. The engine is the host's: mount its socket |
| Trivy | no | yes | `cve_policy` at build |
| Checkov | no | yes | the Checkov policy, in a venv of its own (`/opt/checkov`) so its dependencies never meet strata's |
| Terraform | no | no | its BSL 1.1 licence allows internal use, but shipping it in a public image is not settled (see Out of scope) |
| OpenTofu | no | no | strata does not run `tool: opentofu` yet (`opentofu-support.md` work doc) |

Both variants run as `appuser`, a non-root user with a home directory: git, ssh, Helm (cache, `~/.kube/config`) and
Trivy (its database) write under `$HOME`. `git config --system safe.directory '*'` makes git accept a checkout mounted
with `-v`: it belongs to another user, and without this git refuses it as "dubious ownership", so
`git rev-parse HEAD` fails and the audit record silently loses its commit SHA.

### The Docker engine in the full image

The full image has the Docker CLI, not an engine. A Compose step talks to the host's engine through its socket, and
`appuser` needs the socket's group:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  --group-add "$(stat -c %g /var/run/docker.sock)" \
  -v "$PWD:/work" -w /work ghcr.io/huybrechtsxyz/strata:2.7.0-full deploy run prd-deployment
```

### Pinned inputs

Every input has an exact version, so a build does not depend on the day it ran:

| Input | Where | Kept current by |
| --- | --- | --- |
| `python:3.13-slim` | `FROM` | Dependabot (`docker`) |
| `uv` | `FROM ghcr.io/astral-sh/uv:<v> AS uv` | Dependabot (`docker`) |
| Docker CLI + Compose | `FROM docker:<v>-cli AS docker-cli`, binaries copied | Dependabot (`docker`) |
| Trivy | `FROM aquasec/trivy:<v> AS trivy`, binary copied | Dependabot (`docker`) |
| Checkov | `docker/checkov/requirements.txt` | Dependabot (`pip`, `/docker/checkov`) |
| Helm | `ARG HELM_VERSION`, release archive checked with its `.sha256sum` | by hand: Helm has no official image |
| strata's dependencies | `uv.lock`, `uv sync --locked` | Dependabot (`uv`) |

Dependabot's `docker` ecosystem updates `FROM` lines only, which is why `uv`, the Docker CLI and Trivy are stages of
their own. Its `uv` ecosystem updates `pyproject.toml` and `uv.lock` together; `pip` would update `pyproject.toml`
alone, and `--locked` would then fail the image build. Trivy is pinned to an exact version on purpose: a poisoned
release (v0.69.4) was published through several channels in March 2026.

`tests/strata/test_cli_image.py` checks these rules on the Dockerfile text (what each variant holds, no Terraform or
OpenTofu, slim as the default target, a non-root user with a home, every `FROM` pinned, the lockfile, Helm's checksum),
since the image itself is only built in CI.

### Extending an image

A team that needs another tool, or another version, builds on either variant:

```dockerfile
FROM ghcr.io/huybrechtsxyz/strata:2.7.0-slim
USER root
ARG TERRAFORM_VERSION=1.9.8
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && curl -fsSLo /tmp/tf.zip \
       "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
    && unzip /tmp/tf.zip terraform -d /usr/local/bin && rm /tmp/tf.zip \
    && apt-get purge -y curl unzip && rm -rf /var/lib/apt/lists/*
USER appuser
```

An image built this way for an organization's own pipelines is internal use, which Terraform's licence allows.

### Out of scope

- **Terraform in a published image.** BSL 1.1 allows internal production use and redistribution, but not shipping it
  embedded in a "competitive offering", and whether strata (deployment orchestration with policy gates and audit)
  overlaps HashiCorp's commercial products is a legal question, not settled here. Until it is, no published variant
  carries Terraform; an organization builds its own image (above).
- **OpenTofu in the full image**, until strata runs `tool: opentofu`.
- Grype (Trivy covers `cve_policy`), kubectl (strata never calls it), arm64 builds, base images pinned by digest.

## Related Decisions

- [ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md) - Helm 4 by default, Helm 3 when
  declared: the full image carries Helm 4; a team on Helm 3 extends slim with its own

## History

- The image first held strata only, on the reading that every external tool belongs to the pipeline. strata calls
  git for its own features, though, and without it a `build run` with a remote failed and the audit record lost its
  commit SHA, so git and the ssh client moved in.
- The build first resolved dependencies fresh (`uv.lock` was not copied) with `uv:latest`, so an image could run
  versions CI never tested.
- Since 2.4 and 2.6, `build run` itself calls Helm, so a strata-only image could not build a Helm module properly.
  Putting Helm in the one image would have grown every existing pull; a second, opt-in variant keeps the bare tag
  unchanged. `-full` was chosen over `-toolbox`, `-ci` and `-stack` as the usual partner of `-slim`.
- Terraform was left out of the full image after reading its licence: internal use is clearly allowed, a public
  image that bundles it is not clearly allowed.
