#!/usr/bin/env python3
"""Tests for SBOM PURL/parsing helpers (docs/design/sbom-generation.md Phase 1)."""

from strata.utils.sbom_utils import (
    helm_chart_to_purl,
    image_to_purl,
    is_floating_tag,
    is_local_module_source,
    parse_image_ref,
    terraform_module_to_purl,
    terraform_provider_to_purl,
)

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


def test_terraform_module_to_purl_short_form_public_registry():
    purl = terraform_module_to_purl("terraform-aws-modules/vpc/aws", "5.0.0")
    assert purl == "pkg:terraform/terraform-aws-modules/vpc@5.0.0?repository_url=registry.terraform.io"


def test_terraform_module_to_purl_github_with_ref_query():
    purl = terraform_module_to_purl("github.com/org/repo//subdir?ref=v1.0.0")
    assert purl == "pkg:github/org/repo@v1.0.0"


def test_terraform_provider_to_purl_with_constraint():
    assert terraform_provider_to_purl("hashicorp/azurerm", "~>3.90") == "pkg:terraform/hashicorp/azurerm@~>3.90"
