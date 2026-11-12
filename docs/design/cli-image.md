# strata container image - Design

- Status: implemented
- Last updated: 2026-11-12

## Overview

`Dockerfile.cli` builds the image `ghcr.io/huybrechtsxyz/strata` (`ci-image.yml` on every image build,
`ci-release.yml` on a release). It runs strata in a pipeline without installing Python:
`docker run -v "$PWD:/work" -w /work ghcr.io/huybrechtsxyz/strata validate config`.

The rule for what goes in: **strata and the tools strata itself calls; not the provisioners' tools.** A tool strata
calls for its own features (git for remotes, the commit SHA, audit push and GitOps) is part of strata working at
all. A provisioner's tool (Terraform, OpenTofu, Helm, Docker) is a version each team pins and chooses (Helm 3 or 4,
ADR-0055), so the pipeline brings it, by extending the image.

## Current Design

### Contents

| In the image | Why |
| --- | --- |
| Python 3.13 (`python:3.13-slim`) | strata's runtime |
| strata and its production dependencies, in `/app/.venv` | installed from `uv.lock` with `--locked`, so the image runs the versions CI tested |
| `git` | remotes fetched at build, `git rev-parse HEAD` for the commit SHA in the audit record and the image gate, audit push to a git sink, the GitOps integration |
| `openssh-client` | `auth.method: ssh_key` runs git over ssh; the host key comes from `ssh_key.known_hosts` |
| user `appuser` with a home directory | non-root; git, ssh and any tool added on top (Helm's cache, `~/.kube/config`) write under `$HOME` |
| `git config --system safe.directory '*'` | a checkout mounted with `-v` belongs to another user, and git refuses it ("dubious ownership"): without this, `git rev-parse HEAD` fails and the commit SHA is silently missing |

| Not in the image | Bring it by |
| --- | --- |
| Terraform, OpenTofu | extending the image (below), at the version the root modules pin |
| Helm | extending the image. `build run` calls it too (chart pinning, dependencies, SBOM rendering): without it a Helm build leaves registry charts unpinned and renders nothing |
| Docker / Compose | the pipeline's own Docker, with the host socket |
| Trivy / Grype, Checkov | a separate step or image, for `cve_policy` and the Checkov policy |

### Reproducible builds

- `uv` is copied from `ghcr.io/astral-sh/uv:<version>`, a pinned tag, never `latest`.
- `uv.lock` is copied and `uv sync --locked` installs from it: the build fails if the lock no longer matches
  `pyproject.toml`, instead of resolving newer versions than CI tested.

`tests/strata/test_cli_image.py` checks these rules on the Dockerfile text, since the image itself is only built in
CI.

### Extending the image

```dockerfile
FROM ghcr.io/huybrechtsxyz/strata:2.6.0
USER root
ARG HELM_VERSION=v4.3.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
       | tar -xz -C /usr/local/bin --strip-components=1 linux-amd64/helm \
    && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
USER appuser
```

The same shape adds Terraform (`https://releases.hashicorp.com/terraform/<v>/terraform_<v>_linux_amd64.zip`).

### Out of scope

- A `-helm` variant, Trivy/Grype in the image, arm64 builds and a base image pinned by digest: possible later,
  none asked for yet.
- Terraform in a published image: its BSL licence needs checking before strata redistributes it.

## Related Decisions

- [ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md) - Helm 4 by default, Helm 3 when
  declared: why the Helm version is the pipeline's choice

## History

- The image first held strata only, on the reading that every external tool belongs to the pipeline. strata calls
  git for its own features, though, and without it a `build run` with a remote failed and the audit record lost its
  commit SHA, so git and the ssh client moved in; the provisioners' tools stayed out.
- The build first resolved dependencies fresh (`uv.lock` was not copied) with `uv:latest`, so an image could run
  versions CI never tested.
