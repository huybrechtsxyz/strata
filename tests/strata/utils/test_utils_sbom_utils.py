#!/usr/bin/env python3
"""Tests for SBOM PURL/parsing helpers (docs/design/sbom-generation.md Phase 1)."""

import pytest

from strata.utils.sbom_utils import (
    compose_image_ref,
    exact_version,
    helm_chart_to_purl,
    image_digest_hash,
    image_tag_problem,
    image_to_purl,
    is_floating_tag,
    is_local_module_source,
    parse_image_ref,
    pull_registry,
    registry_host,
    split_image_tag,
    split_registry,
    terraform_module_to_purl,
    terraform_provider_address,
    terraform_provider_to_purl,
)

# ---------------------------------------------------------------------------
# An artifact's image_tag: a tag, a digest or tag@digest (docs/work/artifact-gap-analysis.md, design 4)
# ---------------------------------------------------------------------------

_HEX = "9f86d081" * 8
_DIGEST = f"sha256:{_HEX}"


@pytest.mark.parametrize(
    ("value", "parts", "ref"),
    [
        ("1.4.2", ("1.4.2", None), "example/api:1.4.2"),
        (_DIGEST, (None, _DIGEST), f"example/api@{_DIGEST}"),
        (f"1.4.2@{_DIGEST}", ("1.4.2", _DIGEST), f"example/api:1.4.2@{_DIGEST}"),
        (None, (None, None), "example/api"),
        ("", (None, None), "example/api"),
    ],
)
def test_an_image_tag_splits_and_composes_in_three_shapes(value, parts, ref):
    assert split_image_tag(value) == parts
    assert compose_image_ref("example/api", value) == ref
    if value:
        name, tag, digest = parse_image_ref(ref)
        assert (name, tag, digest) == ("example/api", *parts), "compose is the reverse of parse"


def test_compose_image_ref_keeps_a_registry_port():
    assert compose_image_ref("registry:5000/img", f"1.0@{_DIGEST}") == f"registry:5000/img:1.0@{_DIGEST}"


@pytest.mark.parametrize(
    "value", ["1.4.2", "v1.4.2-rc.1", "env_sbx_20260907.11", _DIGEST, f"1.4.2@{_DIGEST}", "sha512:" + "a" * 128]
)
def test_a_valid_image_tag_has_no_problem(value):
    assert image_tag_problem(value) is None


@pytest.mark.parametrize(
    ("value", "says"),
    [
        ("-1.0", "the tag '-1.0'"),
        ("1.0+build", "the tag '1.0+build'"),
        ("a" * 129, "at most 128 characters"),
        ("sha256:abc123", "the digest 'sha256:abc123'"),
        (f"sha256:{_HEX.upper()}", "lowercase hex"),
        (f"1.4.2@{_HEX}", f"the digest '{_HEX}'"),
        ("1.4.2@", "both sides of '@'"),
        (f"@{_DIGEST}", "both sides of '@'"),
    ],
)
def test_an_invalid_image_tag_says_what_is_wrong(value, says):
    problem = image_tag_problem(value)
    assert problem is not None and says in problem
    assert "a tag (1.4.2), a digest (sha256:<hex>) or both (1.4.2@sha256:<hex>)" in problem


def test_a_tag_with_a_digest_is_never_floating():
    assert is_floating_tag(f"latest@{_DIGEST}") is False
    assert is_floating_tag(_DIGEST) is False


# ---------------------------------------------------------------------------
# split_registry() (docs/design/security-gates.md D1)
# ---------------------------------------------------------------------------


def test_split_registry_recognises_registry_hosts():
    assert split_registry("ghcr.io/org/app") == ("ghcr.io", "org/app")
    assert split_registry("myregistry.azurecr.io/team/api") == ("myregistry.azurecr.io", "team/api")
    assert split_registry("registry:5000/img") == ("registry:5000", "img")
    assert split_registry("localhost/img") == ("localhost", "img")


def test_split_registry_leaves_docker_hub_names_alone():
    assert split_registry("org/app") == (None, "org/app")
    assert split_registry("traefik") == (None, "traefik")


@pytest.mark.parametrize(
    ("image_name", "expected"),
    [
        ("ghcr.io/org/app", "ghcr.io"),
        ("GHCR.io/org/app", "ghcr.io"),
        ("registry:5000/img", "registry:5000"),
        ("localhost/img", "localhost"),
        ("org/app", "docker.io"),
        ("traefik", "docker.io"),
        ("index.docker.io/library/redis", "docker.io"),
    ],
)
def test_pull_registry_names_where_the_runtime_pulls_from(image_name, expected):
    assert pull_registry(image_name) == expected


@pytest.mark.parametrize(
    ("registry", "expected"),
    [
        ("registry.example.com", "registry.example.com"),
        ("https://Registry.Example.com/", "registry.example.com"),
        ("http://acr.example.io/team", "acr.example.io"),
        ("registry:5000", "registry:5000"),
        ("index.docker.io", "docker.io"),
        ("", None),
        ("https://", None),
    ],
)
def test_registry_host_normalises_the_free_text_setting(registry, expected):
    assert registry_host(registry) == expected


# ---------------------------------------------------------------------------
# is_floating_tag()
# ---------------------------------------------------------------------------


def test_is_floating_tag_true_for_none_or_empty():
    assert is_floating_tag(None) is True
    assert is_floating_tag("") is True


def test_is_floating_tag_true_for_known_mutable_aliases():
    assert is_floating_tag("latest") is True
    assert is_floating_tag("Main") is True


def test_is_floating_tag_false_for_a_digest():
    assert is_floating_tag("sha256:abc123") is False


def test_is_floating_tag_false_for_a_semver_tag():
    assert is_floating_tag("v3.0.1") is False
    assert is_floating_tag("2024.12.0") is False


def test_is_floating_tag_true_for_a_non_semver_non_alias_tag():
    assert is_floating_tag("feature-branch") is True


# ---------------------------------------------------------------------------
# parse_image_ref()
# ---------------------------------------------------------------------------


def test_parse_image_ref_name_and_tag():
    assert parse_image_ref("traefik:v3.0.1") == ("traefik", "v3.0.1", None)


def test_parse_image_ref_registry_with_path():
    assert parse_image_ref("ghcr.io/org/app:v1.2.3") == ("ghcr.io/org/app", "v1.2.3", None)


def test_parse_image_ref_digest():
    assert parse_image_ref("postgres@sha256:abc123") == ("postgres", None, "sha256:abc123")


def test_parse_image_ref_registry_port_not_mistaken_for_tag():
    assert parse_image_ref("registry:5000/img:latest") == ("registry:5000/img", "latest", None)


def test_parse_image_ref_no_tag_no_digest():
    assert parse_image_ref("redis") == ("redis", None, None)


# ---------------------------------------------------------------------------
# image_to_purl()
# ---------------------------------------------------------------------------


def test_image_to_purl_with_tag():
    assert image_to_purl("traefik:v3.0.1") == "pkg:docker/traefik@v3.0.1"


def test_image_to_purl_with_digest():
    assert image_to_purl("postgres@sha256:abc123") == "pkg:docker/postgres@sha256:abc123"


def test_image_to_purl_with_neither():
    assert image_to_purl("redis") == "pkg:docker/redis"


@pytest.mark.parametrize(
    ("image", "purl"),
    [
        ("ghcr.io/goauthentik/server:2026.5.2", "pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io"),
        ("registry:5000/img:1.0", "pkg:docker/img@1.0?repository_url=registry:5000"),
        ("acr.example.io/team/api@sha256:abc123", "pkg:docker/team/api@sha256:abc123?repository_url=acr.example.io"),
        ("portainer/portainer-ce:2.39.3-alpine", "pkg:docker/portainer/portainer-ce@2.39.3-alpine"),
    ],
)
def test_image_to_purl_puts_the_registry_in_a_repository_url_qualifier(image, purl):
    """purl-spec `docker` type: the name is the repository, the registry is the `repository_url` qualifier."""
    assert image_to_purl(image) == purl


@pytest.mark.parametrize(
    "image",
    [
        "postgres:16-alpine",
        "library/postgres:16-alpine",
        "docker.io/library/postgres:16-alpine",
        "index.docker.io/library/postgres:16-alpine",
    ],
)
def test_image_to_purl_gives_every_spelling_of_a_docker_hub_image_one_purl(image):
    assert image_to_purl(image) == "pkg:docker/postgres@16-alpine"


def test_image_to_purl_keeps_a_docker_hub_organisation():
    assert image_to_purl("docker.io/bitnami/redis:7.2") == "pkg:docker/bitnami/redis@7.2"


_SHA256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (f"postgres@sha256:{_SHA256}", ("SHA-256", _SHA256)),
        (f"ghcr.io/acme/api:1.4.2@sha256:{_SHA256}", ("SHA-256", _SHA256)),
        (f"registry:5000/img@sha256:{_SHA256}", ("SHA-256", _SHA256)),
        ("img@sha384:" + "b" * 96, ("SHA-384", "b" * 96)),
        ("img@sha512:" + "c" * 128, ("SHA-512", "c" * 128)),
    ],
)
def test_image_digest_hash_returns_the_cyclonedx_algorithm_and_hex(image, expected):
    assert image_digest_hash(image) == expected


@pytest.mark.parametrize(
    "image",
    [
        "redis:7-alpine",
        "redis",
        "registry:5000/img:1.0",
        "img@md5:" + "a" * 32,
        "img@sha256:abc123",
        "img@sha256:" + "a" * 65,
        "img@sha256:" + _SHA256.upper(),
        "img@sha256:" + "g" * 64,
        "img@" + _SHA256,
    ],
)
def test_image_digest_hash_is_none_without_a_valid_digest(image):
    """A tag-only image, an unknown algorithm, or a digest that breaks the OCI form records no hash."""
    assert image_digest_hash(image) is None


# ---------------------------------------------------------------------------
# helm_chart_to_purl()
# ---------------------------------------------------------------------------


def test_helm_chart_to_purl_with_version_and_repository():
    purl = helm_chart_to_purl("authentik", "2024.12.0", "https://charts.goauthentik.io")
    # quote(..., safe=":/") leaves ':' and '/' unescaped — only used to encode
    # anything else that might appear in a repository URL (spaces, etc.).
    assert purl == "pkg:helm/authentik@2024.12.0?repository_url=https://charts.goauthentik.io"


def test_helm_chart_to_purl_name_only():
    assert helm_chart_to_purl("authentik", None) == "pkg:helm/authentik"


# ---------------------------------------------------------------------------
# is_local_module_source() / terraform_module_to_purl() / terraform_provider_to_purl()
# ---------------------------------------------------------------------------


def test_is_local_module_source_true_for_relative_paths():
    assert is_local_module_source("./modules/net") is True
    assert is_local_module_source("../shared/net") is True


def test_is_local_module_source_false_for_a_registry_source():
    assert is_local_module_source("terraform-aws-modules/vpc/aws") is False


def test_terraform_module_to_purl_local_is_none():
    assert terraform_module_to_purl("./modules/net") is None


@pytest.mark.parametrize(
    ("constraint", "exact"),
    [
        ("5.0.0", "5.0.0"),
        ("v5.0.0", "v5.0.0"),
        ("1.2.3-beta.1", "1.2.3-beta.1"),
        ("= 5.0.0", "5.0.0"),
        ("=5.0.0", "5.0.0"),
        (" 5.0.0 ", "5.0.0"),
        ("~> 5.0", None),
        (">= 5.0, < 6.0", None),
        ("!= 5.1.0", None),
        ("5.0.0, 5.1.0", None),
        ("latest", None),
        ("", None),
        (None, None),
    ],
)
def test_exact_version_is_only_a_single_pinned_version(constraint, exact):
    assert exact_version(constraint) == exact


def test_terraform_module_to_purl_short_form_public_registry():
    purl = terraform_module_to_purl("terraform-aws-modules/vpc/aws", "5.0.0")
    assert purl == "pkg:terraform/terraform-aws-modules/vpc@5.0.0?repository_url=registry.terraform.io"


def test_terraform_module_to_purl_github_with_ref_query():
    purl = terraform_module_to_purl("github.com/org/repo//subdir?ref=v1.0.0")
    assert purl == "pkg:github/org/repo@v1.0.0"


@pytest.mark.parametrize(
    ("source", "address"),
    [
        ("hashicorp/azurerm", "registry.terraform.io/hashicorp/azurerm"),
        ("azurerm", "registry.terraform.io/hashicorp/azurerm"),
        ("registry.terraform.io/hashicorp/azurerm", "registry.terraform.io/hashicorp/azurerm"),
        ("Registry.OpenTofu.org/Hashicorp/Null", "registry.opentofu.org/hashicorp/null"),
    ],
)
def test_terraform_provider_address_applies_terraforms_defaults(source, address):
    assert terraform_provider_address(source) == address


def test_terraform_provider_purl_leaves_out_the_default_registry_and_qualifies_another():
    assert terraform_provider_to_purl("registry.terraform.io/hashicorp/azurerm", "4.12.0") == (
        "pkg:terraform/hashicorp/azurerm@4.12.0"
    )
    assert terraform_provider_to_purl("registry.opentofu.org/hashicorp/null", "3.2.1") == (
        "pkg:terraform/hashicorp/null@3.2.1?repository_url=registry.opentofu.org"
    )
    assert terraform_provider_to_purl("hashicorp/azurerm") == "pkg:terraform/hashicorp/azurerm"
