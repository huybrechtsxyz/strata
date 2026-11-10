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

from packageurl import PackageURL

#: Tags that indicate a floating (mutable) image reference.
MUTABLE_ALIAS_TAGS: frozenset[str] = frozenset(
    {"latest", "main", "master", "dev", "develop", "staging", "edge", "nightly", "canary"}
)

#: Semver-ish pattern: optional leading "v", dot-separated integers,
#: optional pre-release / build metadata suffix.
_SEMVER_RE = re.compile(r"^v?\d+(\.\d+)*(-[a-zA-Z0-9.+]+)?(\+[a-zA-Z0-9.+]+)?$")

#: A version constraint that allows exactly one version: a bare version, or Terraform's `=` operator.
_EXACT_CONSTRAINT_RE = re.compile(r"=?\s*(v?\d+(\.\d+)*(-[a-zA-Z0-9.+]+)?(\+[a-zA-Z0-9.+]+)?)")


def exact_version(constraint: str | None) -> str | None:
    """The one version `constraint` allows, or `None` for a range (`~> 5.0`, `>= 5, < 6`) or nothing.

    Examples:
        "5.0.0" -> "5.0.0"; "= 5.0.0" -> "5.0.0"; "~> 5.0" -> None
    """
    match = _EXACT_CONSTRAINT_RE.fullmatch(constraint.strip()) if constraint else None
    return match.group(1) if match else None


def compose_image_ref(image_name: str, image_tag: str | None) -> str:
    """`name:tag`, `name@algo:digest` for a digest, or the bare name when there is no tag; the reverse of
    `parse_image_ref()`.

    A tag never contains `:`, so a value that does is a digest (`sha256:...`).
    """
    if not image_tag:
        return image_name
    separator = "@" if ":" in image_tag else ":"
    return f"{image_name}{separator}{image_tag}"


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
    if tag.lower() in MUTABLE_ALIAS_TAGS:
        return True
    if _SEMVER_RE.match(tag):
        return False
    return True


def is_mutable_alias_tag(tag: str | None) -> bool:
    """True for a tag that is a known mutable alias (`latest`, `main`, `dev`, ...).

    Unlike `is_floating_tag()`, a tag that merely is not semver-shaped (a build number such as
    `env_sbx_20260907.11`) is not mutable: used for a tag a `kind: version` pin chose on purpose.
    """
    return tag is not None and tag.lower() in MUTABLE_ALIAS_TAGS


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


def split_registry(name: str) -> tuple[str | None, str]:
    """Split an image name (no tag or digest) into `(registry, repository)`.

    The first path segment is a registry when it contains `.` or `:` or is `localhost`, which is
    Docker's own rule; otherwise the image is on Docker Hub and the registry is `None`.

    Examples:
        "ghcr.io/org/app" -> ("ghcr.io", "org/app")
        "registry:5000/img" -> ("registry:5000", "img")
        "org/app" -> (None, "org/app")
        "traefik" -> (None, "traefik")
    """
    first, separator, rest = name.partition("/")
    if separator and ("." in first or ":" in first or first == "localhost"):
        return first, rest
    return None, name


#: The names Docker Hub answers to; an image on any of them is written without a registry.
_DOCKER_HUB_REGISTRIES: frozenset[str] = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})


def image_to_purl(image: str) -> str:
    """Convert a container image reference to a Package URL string (purl-spec `docker` type).

    The name is the repository; a registry other than Docker Hub goes in the `repository_url`
    qualifier, and Docker Hub's implicit `library/` is dropped, so every spelling of one image
    gives one purl. Uses the digest when present; falls back to the tag.

    Examples:
        "traefik:v3.0.1" -> "pkg:docker/traefik@v3.0.1"
        "docker.io/library/postgres:16" -> "pkg:docker/postgres@16"
        "ghcr.io/org/app:1.0" -> "pkg:docker/org/app@1.0?repository_url=ghcr.io"
        "postgres@sha256:abc123" -> "pkg:docker/postgres@sha256:abc123"
    """
    name, tag, digest = parse_image_ref(image)
    registry, repository = split_registry(name)
    if registry in _DOCKER_HUB_REGISTRIES:
        registry = None
    if registry is None:
        repository = repository.removeprefix("library/")
    namespace, _, short_name = repository.rpartition("/")
    return PackageURL(
        type="docker",
        namespace=namespace or None,
        name=short_name,
        version=digest or tag,
        qualifiers={"repository_url": registry} if registry else None,
    ).to_string()


#: OCI digest algorithm -> (CycloneDX hash algorithm name, hex length).
_DIGEST_ALGORITHMS: dict[str, tuple[str, int]] = {
    "sha256": ("SHA-256", 64),
    "sha384": ("SHA-384", 96),
    "sha512": ("SHA-512", 128),
}
_LOWER_HEX_RE = re.compile(r"[0-9a-f]+")


def image_digest_hash(image: str) -> tuple[str, str] | None:
    """The `(CycloneDX algorithm, hex)` of the digest an image reference carries, or `None`.

    `None` without a digest, for an algorithm CycloneDX has no name for, and for a digest that is not
    lowercase hex of the algorithm's length (the OCI form).

    Example:
        "postgres:16@sha256:9f86..." -> ("SHA-256", "9f86...")
    """
    _, _, digest = parse_image_ref(image)
    algorithm, _, content = (digest or "").partition(":")
    known = _DIGEST_ALGORITHMS.get(algorithm)
    if known is None or len(content) != known[1] or not _LOWER_HEX_RE.fullmatch(content):
        return None
    return known[0], content


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


#: Where Terraform looks for a provider whose source names no host.
TERRAFORM_DEFAULT_REGISTRY = "registry.terraform.io"


def terraform_provider_address(source: str) -> str:
    """The full `host/namespace/type` address of a provider source, as `.terraform.lock.hcl` keys it.

    Applies Terraform's defaults: no host is `registry.terraform.io`, no namespace is `hashicorp`.
    Lower-cased, because Terraform compares addresses case-insensitively.

    Examples:
        "hashicorp/azurerm" -> "registry.terraform.io/hashicorp/azurerm"
        "azurerm" -> "registry.terraform.io/hashicorp/azurerm"
    """
    parts = source.strip().lower().split("/")
    if len(parts) == 1:
        parts = [TERRAFORM_DEFAULT_REGISTRY, "hashicorp", *parts]
    elif len(parts) == 2:
        parts = [TERRAFORM_DEFAULT_REGISTRY, *parts]
    return "/".join(parts)


def terraform_provider_to_purl(source: str, version: str | None = None) -> str:
    """Convert a Terraform provider source to a Package URL string.

    `version` should be a real version (the one `.terraform.lock.hcl` locked), not a constraint.
    A registry other than `registry.terraform.io` goes in the `repository_url` qualifier.

    Examples:
        terraform_provider_to_purl("hashicorp/azurerm", "4.12.0") -> "pkg:terraform/hashicorp/azurerm@4.12.0"
        terraform_provider_to_purl("registry.opentofu.org/hashicorp/null")
        -> "pkg:terraform/hashicorp/null?repository_url=registry.opentofu.org"
    """
    host, namespace, name = terraform_provider_address(source).split("/", 2)
    return PackageURL(
        type="terraform",
        namespace=namespace,
        name=name,
        version=version,
        qualifiers={"repository_url": host} if host != TERRAFORM_DEFAULT_REGISTRY else None,
    ).to_string()


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
