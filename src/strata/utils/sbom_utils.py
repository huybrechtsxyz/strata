#!/usr/bin/env python3
"""Pure-function helpers for SBOM component generation (docs/design/
sbom-generation.md Phase 1).

Ported from v1's `utils/sbom_utils.py`, unchanged logic — side-effect-free
string operations only, no model/controller/integration imports, so every
`strata.integrations.sbom_collectors` collector can depend on this without
pulling in anything heavier.
"""

import re
from urllib.parse import quote

#: Tags that indicate a floating (mutable) image reference.
_FLOATING_TAGS: frozenset[str] = frozenset(
    {"latest", "main", "master", "dev", "develop", "staging", "edge", "nightly", "canary"}
)

#: Semver-ish pattern: optional leading "v", dot-separated integers,
#: optional pre-release / build metadata suffix.
_SEMVER_RE = re.compile(r"^v?\d+(\.\d+)*(-[a-zA-Z0-9.+]+)?(\+[a-zA-Z0-9.+]+)?$")


def is_floating_tag(tag: str | None) -> bool:
    """Return True when the image tag is mutable / not pinned.

    A tag is considered floating when it is None/empty, an exact match for a
    known mutable alias (latest, main, dev, …), or doesn't look like a
    semantic version string. A digest reference (`sha256:...`) is never
    floating.
    """
    if not tag:
        return True
    if tag.startswith("sha256:"):
        return False
    if tag.lower() in _FLOATING_TAGS:
        return True
    if _SEMVER_RE.match(tag):
        return False
    return True


def parse_image_ref(image: str) -> tuple[str, str | None, str | None]:
    """Parse an image reference into `(name, tag, digest)`.

    `name` includes any registry prefix and path components.

    Examples:
        "traefik:v3.0.1" -> ("traefik", "v3.0.1", None)
        "ghcr.io/org/app:v1.2.3" -> ("ghcr.io/org/app", "v1.2.3", None)
        "postgres@sha256:abc123" -> ("postgres", None, "sha256:abc123")
        "registry:5000/img:latest" -> ("registry:5000/img", "latest", None)
    """
    digest: str | None = None
    tag: str | None = None

    if "@" in image:
        ref, digest = image.rsplit("@", 1)
    else:
        ref = image

    # Find tag: the colon *after* the last slash (to avoid matching registry port).
    last_slash = ref.rfind("/")
    colon_pos = ref.find(":", last_slash + 1)
    if colon_pos != -1:
        name = ref[:colon_pos]
        tag = ref[colon_pos + 1 :]
    else:
        name = ref

    return name, tag, digest


def image_to_purl(image: str) -> str:
    """Convert a container image reference to a Package URL string.

    Uses the digest when present; falls back to the tag.

    Examples:
        "traefik:v3.0.1" -> "pkg:docker/traefik@v3.0.1"
        "postgres@sha256:abc123" -> "pkg:docker/postgres@sha256:abc123"
    """
    name, tag, digest = parse_image_ref(image)
    if digest:
        return f"pkg:docker/{name}@{digest}"
    if tag:
        return f"pkg:docker/{name}@{tag}"
    return f"pkg:docker/{name}"


def helm_chart_to_purl(name: str, version: str | None, repository: str | None = None) -> str:
    """Convert a Helm chart reference to a Package URL string.

    Example:
        helm_chart_to_purl("authentik", "2024.12.0", "https://charts.goauthentik.io")
        -> "pkg:helm/authentik@2024.12.0?repository_url=https%3A//charts.goauthentik.io"
    """
    purl = f"pkg:helm/{name}"
    if version:
        purl += f"@{version}"
    if repository:
        purl += f"?repository_url={quote(repository, safe=':/')}"
    return purl


def is_local_module_source(source: str) -> bool:
    """Return True if `source` is a local Terraform module reference (`./` or `../`).

    Local modules live in the same repo, not a registry — not a publishable
    SBOM component.
    """
    return source.startswith("./") or source.startswith("../")


def terraform_provider_to_purl(source: str, version: str | None = None) -> str:
    """Convert a Terraform provider source to a Package URL string.

    `version` is typically a constraint string (e.g. `~>5.0`), not a resolved
    version — recorded as-is.

    Example:
        terraform_provider_to_purl("hashicorp/azurerm", "~>3.90")
        -> "pkg:terraform/hashicorp/azurerm@~>3.90"
    """
    purl = f"pkg:terraform/{source}"
    if version:
        purl += f"@{version}"
    return purl


def terraform_module_to_purl(source: str, version: str | None = None) -> str | None:
    """Convert a Terraform module source string to a Package URL.

    Returns `None` for local modules (`./`/`../`) and unsupported source
    formats (e.g. Bitbucket, unknown hosts) — the caller decides whether to
    warn on a `None` return.

    Supported source formats:
        - `registry.terraform.io/namespace/module/provider`
        - `namespace/module/provider` (short public-registry form)
        - `github.com/org/repo[//subdir][?ref=tag]`
    """
    if is_local_module_source(source):
        return None

    base = source.split("//")[0]

    if base.startswith("github.com/"):
        ref_match = re.search(r"\?ref=([^&\s]+)", source)
        ref = (ref_match.group(1) if ref_match else None) or version
        path = base.split("?")[0]
        parts = path.split("/")
        if len(parts) < 3:
            return None
        org_repo = f"{parts[1]}/{parts[2]}"
        purl = f"pkg:github/{org_repo}"
        if ref:
            purl += f"@{ref}"
        return purl

    if base.startswith("registry.terraform.io/"):
        remainder = base[len("registry.terraform.io/") :]
        parts = [p for p in remainder.split("/") if p]
        if len(parts) < 2:
            return None
        name = f"{parts[0]}/{parts[1]}"
        purl = f"pkg:terraform/{name}"
        if version:
            purl += f"@{version}"
        purl += "?repository_url=registry.terraform.io"
        return purl

    parts = [p for p in base.split("/") if p]
    if len(parts) == 3 and "." not in parts[0]:
        name = f"{parts[0]}/{parts[1]}"
        purl = f"pkg:terraform/{name}"
        if version:
            purl += f"@{version}"
        purl += "?repository_url=registry.terraform.io"
        return purl

    return None
