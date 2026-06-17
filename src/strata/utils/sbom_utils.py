"""Pure-function utilities for SBOM component generation.

No imports from builders/, services/, or integrations/.
All functions are side-effect-free string/path operations.
"""

import re
from typing import Optional
from urllib.parse import quote

# Tags that indicate a floating (mutable) image reference.
_FLOATING_TAGS: frozenset[str] = frozenset(
    {"latest", "main", "master", "dev", "develop", "staging", "edge", "nightly", "canary"}
)

# Semver-ish pattern: optional leading "v", dot-separated integers,
# optional pre-release / build metadata suffix.
_SEMVER_RE = re.compile(r"^v?\d+(\.\d+)*(-[a-zA-Z0-9.+]+)?(\+[a-zA-Z0-9.+]+)?$")


# ---------------------------------------------------------------------------
# Floating-tag detection
# ---------------------------------------------------------------------------


def is_floating_tag(tag: Optional[str]) -> bool:
    """Return True when the image tag is mutable / not pinned.

    A tag is considered floating when it:
    - is None or empty
    - is an exact match for a known mutable alias (latest, main, dev, …)
    - does not look like a semantic version string

    A digest reference (``sha256:...``) is never floating.
    """
    if not tag:
        return True
    if tag.startswith("sha256:"):
        return False
    if tag.lower() in _FLOATING_TAGS:
        return True
    if _SEMVER_RE.match(tag):
        return False
    # Non-semver, non-digest, non-known-alias → treat as floating
    return True


# ---------------------------------------------------------------------------
# Image reference parser (internal helper)
# ---------------------------------------------------------------------------


def parse_image_ref(image: str) -> tuple[str, Optional[str], Optional[str]]:
    """Parse an image reference into ``(name, tag, digest)``.

    *name* includes any registry prefix and path components.

    Examples::

        "traefik:v3.0.1"              → ("traefik", "v3.0.1", None)
        "ghcr.io/org/app:v1.2.3"      → ("ghcr.io/org/app", "v1.2.3", None)
        "postgres@sha256:abc123"       → ("postgres", None, "sha256:abc123")
        "registry:5000/img:latest"     → ("registry:5000/img", "latest", None)
    """
    digest: Optional[str] = None
    tag: Optional[str] = None

    # Digest takes precedence
    if "@" in image:
        ref, digest = image.rsplit("@", 1)
    else:
        ref = image

    # Find tag: the colon *after* the last slash (to avoid matching registry port)
    last_slash = ref.rfind("/")
    colon_pos = ref.find(":", last_slash + 1)
    if colon_pos != -1:
        name = ref[:colon_pos]
        tag = ref[colon_pos + 1 :]
    else:
        name = ref

    return name, tag, digest


# ---------------------------------------------------------------------------
# PURL builders
# ---------------------------------------------------------------------------


def image_to_purl(image: str) -> str:
    """Convert a container image reference to a Package URL string.

    Uses the digest when present; falls back to the tag.

    Examples::

        "traefik:v3.0.1"          → "pkg:docker/traefik@v3.0.1"
        "ghcr.io/org/app:v1.2.3"  → "pkg:docker/ghcr.io/org/app@v1.2.3"
        "postgres@sha256:abc123"  → "pkg:docker/postgres@sha256:abc123"
    """
    name, tag, digest = parse_image_ref(image)
    if digest:
        return f"pkg:docker/{name}@{digest}"
    if tag:
        return f"pkg:docker/{name}@{tag}"
    return f"pkg:docker/{name}"


def helm_chart_to_purl(
    name: str,
    version: Optional[str],
    repository: Optional[str] = None,
) -> str:
    """Convert a Helm chart reference to a Package URL string.

    Example::

        helm_chart_to_purl("authentik", "2024.12.0", "https://charts.goauthentik.io")
        → "pkg:helm/authentik@2024.12.0?repository_url=https%3A//charts.goauthentik.io"
    """
    purl = f"pkg:helm/{name}"
    if version:
        purl += f"@{version}"
    if repository:
        purl += f"?repository_url={quote(repository, safe=':/')}"
    return purl


def terraform_module_to_purl(source: str, version: Optional[str] = None) -> Optional[str]:
    """Convert a Terraform module source string to a Package URL.

    Returns ``None`` for local modules (``./`` or ``../``) and unsupported
    source formats (e.g. Bitbucket, unknown hosts).  The caller is responsible
    for deciding whether to warn on a ``None`` return.

    Supported source formats:

    - ``registry.terraform.io/namespace/module/provider`` →
      ``pkg:terraform/namespace/module@version?repository_url=registry.terraform.io``
    - ``namespace/module/provider`` (short public-registry form) →
      same, with ``registry.terraform.io`` assumed
    - ``github.com/org/repo[//subdir][?ref=tag]`` →
      ``pkg:github/org/repo@ref`` (``?ref=`` query param or *version* attribute)
    """
    if source.startswith("./") or source.startswith("../"):
        return None

    # Strip subdirectory suffix (//subdir)
    base = source.split("//")[0]

    # ---- GitHub --------------------------------------------------------
    if base.startswith("github.com/"):
        # Search for ?ref= in the original source (may follow the //subdir suffix)
        ref_match = re.search(r"\?ref=([^&\s]+)", source)
        ref = (ref_match.group(1) if ref_match else None) or version
        path = base.split("?")[0]  # strip query string for path parsing
        parts = path.split("/")  # ["github.com", "org", "repo", ...]
        if len(parts) < 3:
            return None
        org_repo = f"{parts[1]}/{parts[2]}"
        purl = f"pkg:github/{org_repo}"
        if ref:
            purl += f"@{ref}"
        return purl

    # ---- Explicit Terraform registry -----------------------------------
    if base.startswith("registry.terraform.io/"):
        remainder = base[len("registry.terraform.io/") :]
        parts = [p for p in remainder.split("/") if p]
        if len(parts) < 2:
            return None
        name = f"{parts[0]}/{parts[1]}"  # namespace/module (drop provider suffix)
        purl = f"pkg:terraform/{name}"
        if version:
            purl += f"@{version}"
        purl += "?repository_url=registry.terraform.io"
        return purl

    # ---- Short-form public registry: namespace/module/provider ---------
    parts = [p for p in base.split("/") if p]
    if len(parts) == 3 and "." not in parts[0]:
        name = f"{parts[0]}/{parts[1]}"
        purl = f"pkg:terraform/{name}"
        if version:
            purl += f"@{version}"
        purl += "?repository_url=registry.terraform.io"
        return purl

    # Unsupported source format (Bitbucket, other Git hosts, etc.)
    return None


def terraform_provider_to_purl(source: str, version: Optional[str] = None) -> str:
    """Convert a Terraform provider source to a Package URL string.

    *version* is typically a constraint string (e.g. ``~>5.0``), not a resolved
    version.  It is recorded as-is — resolution would require a lock file.

    Examples::

        terraform_provider_to_purl("hashicorp/azurerm", "~>3.90")
        → "pkg:terraform/hashicorp/azurerm@~>3.90"
    """
    purl = f"pkg:terraform/{source}"
    if version:
        purl += f"@{version}"
    return purl


def ansible_collection_to_purl(name: str, version: Optional[str] = None) -> str:
    """Convert an Ansible collection name to a Package URL string.

    Collection names use ``namespace.collection`` dot notation (Galaxy convention).

    Example::

        ansible_collection_to_purl("community.general", "7.0.0")
        → "pkg:ansible/community.general@7.0.0"
    """
    purl = f"pkg:ansible/{name}"
    if version:
        purl += f"@{version}"
    return purl


def ansible_role_to_purl(name: str, version: Optional[str] = None) -> str:
    """Convert an Ansible role name to a Package URL string.

    Role names use ``author.rolename`` dot notation (Galaxy install convention).

    Example::

        ansible_role_to_purl("geerlingguy.docker", "6.0.0")
        → "pkg:ansible/geerlingguy.docker@6.0.0"
    """
    purl = f"pkg:ansible/{name}"
    if version:
        purl += f"@{version}"
    return purl
