"""Unit tests for sbom_utils — PURL helpers and floating-tag detection."""

from strata.utils.sbom_utils import (
    ansible_collection_to_purl,
    ansible_role_to_purl,
    helm_chart_to_purl,
    image_to_purl,
    is_floating_tag,
    parse_image_ref,
    terraform_provider_to_purl,
)


class TestIsFloatingTag:
    def test_none_is_floating(self):
        assert is_floating_tag(None) is True

    def test_empty_string_is_floating(self):
        assert is_floating_tag("") is True

    def test_latest_is_floating(self):
        assert is_floating_tag("latest") is True

    def test_main_is_floating(self):
        assert is_floating_tag("main") is True

    def test_dev_is_floating(self):
        assert is_floating_tag("dev") is True

    def test_edge_is_floating(self):
        assert is_floating_tag("edge") is True

    def test_staging_is_floating(self):
        assert is_floating_tag("staging") is True

    def test_semver_is_pinned(self):
        assert is_floating_tag("v3.0.1") is False

    def test_semver_no_v_prefix_is_pinned(self):
        assert is_floating_tag("3.0.1") is False

    def test_semver_major_only_is_pinned(self):
        assert is_floating_tag("v3") is False

    def test_digest_is_pinned(self):
        assert is_floating_tag("sha256:abc123def456") is False

    def test_arbitrary_string_is_floating(self):
        assert is_floating_tag("mybranch") is True

    def test_case_insensitive(self):
        assert is_floating_tag("LATEST") is True
        assert is_floating_tag("Main") is True


class TestParseImageRef:
    def test_name_and_tag(self):
        name, tag, digest = parse_image_ref("traefik:v3.0.1")
        assert name == "traefik"
        assert tag == "v3.0.1"
        assert digest is None

    def test_registry_and_tag(self):
        name, tag, digest = parse_image_ref("ghcr.io/org/app:v1.2.3")
        assert name == "ghcr.io/org/app"
        assert tag == "v1.2.3"
        assert digest is None

    def test_digest_only(self):
        name, tag, digest = parse_image_ref("postgres@sha256:abc123")
        assert name == "postgres"
        assert tag is None
        assert digest == "sha256:abc123"

    def test_name_only(self):
        name, tag, digest = parse_image_ref("postgres")
        assert name == "postgres"
        assert tag is None
        assert digest is None

    def test_registry_with_port(self):
        name, tag, digest = parse_image_ref("registry:5000/img:latest")
        assert name == "registry:5000/img"
        assert tag == "latest"
        assert digest is None


class TestImageToPurl:
    def test_name_and_tag(self):
        purl = image_to_purl("traefik:v3.0.1")
        assert purl == "pkg:docker/traefik@v3.0.1"

    def test_registry_path_and_tag(self):
        purl = image_to_purl("ghcr.io/org/app:v1.2.3")
        assert purl == "pkg:docker/ghcr.io/org/app@v1.2.3"

    def test_digest_preferred_over_tag(self):
        purl = image_to_purl("postgres@sha256:abc123")
        assert purl == "pkg:docker/postgres@sha256:abc123"

    def test_name_only_no_version(self):
        purl = image_to_purl("postgres")
        assert purl == "pkg:docker/postgres"


class TestHelmChartToPurl:
    def test_with_version_and_repo(self):
        purl = helm_chart_to_purl("authentik", "2024.12.0", "https://charts.goauthentik.io")
        assert purl.startswith("pkg:helm/authentik@2024.12.0")
        assert "repository_url=" in purl

    def test_with_version_no_repo(self):
        purl = helm_chart_to_purl("nginx", "18.2.0")
        assert purl == "pkg:helm/nginx@18.2.0"

    def test_no_version(self):
        purl = helm_chart_to_purl("nginx", None)
        assert purl == "pkg:helm/nginx"

    def test_version_and_no_repo(self):
        purl = helm_chart_to_purl("traefik", "28.0.0", None)
        assert purl == "pkg:helm/traefik@28.0.0"


class TestTerraformProviderToPurl:
    def test_with_source_and_version(self):
        purl = terraform_provider_to_purl("hashicorp/azurerm", "~>3.90")
        assert purl == "pkg:terraform/hashicorp/azurerm@~>3.90"

    def test_source_only(self):
        purl = terraform_provider_to_purl("hetznercloud/hcloud")
        assert purl == "pkg:terraform/hetznercloud/hcloud"

    def test_version_none(self):
        purl = terraform_provider_to_purl("hashicorp/aws", None)
        assert purl == "pkg:terraform/hashicorp/aws"


class TestAnsibleCollectionToPurl:
    def test_collection_with_version(self):
        purl = ansible_collection_to_purl("community.general", "7.0.0")
        assert purl == "pkg:ansible/community.general@7.0.0"

    def test_collection_no_version(self):
        purl = ansible_collection_to_purl("ansible.posix")
        assert purl == "pkg:ansible/ansible.posix"


class TestAnsibleRoleToPurl:
    def test_role_with_version(self):
        purl = ansible_role_to_purl("geerlingguy.docker", "6.0.0")
        assert purl == "pkg:ansible/geerlingguy.docker@6.0.0"

    def test_role_no_version(self):
        purl = ansible_role_to_purl("geerlingguy.nginx")
        assert purl == "pkg:ansible/geerlingguy.nginx"
