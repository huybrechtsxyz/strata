"""Unit tests for the tenant-file resolution helpers in path_convention.py.

Covers build_path_from_pattern() (the reverse of match_pattern()),
find_tenant_path_pattern(), resolve_tenant_relative_path(), and
resolve_tenant_file_path() — the config-driven tenant path resolution
that replaces the previously hardcoded ``tenants/{code}.yaml`` convention.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strata.utils.path_convention import (
    build_path_from_pattern,
    find_tenant_path_pattern,
    resolve_tenant_file_path,
    resolve_tenant_relative_path,
)


class TestBuildPathFromPattern:
    def test_single_segment_substitution(self):
        assert build_path_from_pattern("tenants/{code}.yaml", code="acme") == "tenants/acme.yaml"

    def test_nested_segment_substitution(self):
        result = build_path_from_pattern("customers/{code}/customer.yaml", code="acme")
        assert result == "customers/acme/customer.yaml"

    def test_multiple_segments(self):
        result = build_path_from_pattern("zones/{zone}/customers/{code}", zone="eu", code="acme")
        assert result == "zones/eu/customers/acme"

    def test_missing_value_raises(self):
        with pytest.raises(ValueError, match="code"):
            build_path_from_pattern("tenants/{code}.yaml")

    def test_no_segments_returns_literal(self):
        assert build_path_from_pattern("tenants/fixed.yaml") == "tenants/fixed.yaml"


class TestFindTenantPathPattern:
    def test_none_when_no_configuration_model(self):
        assert find_tenant_path_pattern(None) is None

    def test_none_when_no_paths(self):
        config_model = MagicMock()
        config_model.spec.paths = None
        assert find_tenant_path_pattern(config_model) is None

    def test_none_when_no_convention_resolves_tenant(self):
        conv = MagicMock()
        conv.resolves = None
        config_model = MagicMock()
        config_model.spec.paths = [conv]
        assert find_tenant_path_pattern(config_model) is None

    def test_returns_pattern_when_declared(self):
        conv = MagicMock()
        conv.resolves = "tenant"
        conv.pattern = "customers/{code}/customer.yaml"
        config_model = MagicMock()
        config_model.spec.paths = [conv]
        assert find_tenant_path_pattern(config_model) == "customers/{code}/customer.yaml"

    def test_skips_non_tenant_conventions_to_find_tenant_one(self):
        other = MagicMock()
        other.resolves = None
        tenant_conv = MagicMock()
        tenant_conv.resolves = "tenant"
        tenant_conv.pattern = "custom/{code}.yaml"
        config_model = MagicMock()
        config_model.spec.paths = [other, tenant_conv]
        assert find_tenant_path_pattern(config_model) == "custom/{code}.yaml"


class TestResolveTenantRelativePath:
    def test_falls_back_to_builtin_when_no_configuration_model(self):
        assert resolve_tenant_relative_path("acme", None) == "tenants/acme.yaml"

    def test_falls_back_to_builtin_when_no_convention_declared(self):
        config_model = MagicMock()
        config_model.spec.paths = None
        assert resolve_tenant_relative_path("acme", config_model) == "tenants/acme.yaml"

    def test_uses_declared_convention_pattern(self):
        conv = MagicMock()
        conv.resolves = "tenant"
        conv.pattern = "customers/{code}/customer.yaml"
        config_model = MagicMock()
        config_model.spec.paths = [conv]
        assert resolve_tenant_relative_path("acme", config_model) == "customers/acme/customer.yaml"


class TestResolveTenantFilePath:
    def test_builds_absolute_path_with_builtin_default(self, tmp_path):
        result = resolve_tenant_file_path(tmp_path, "acme")
        assert result == tmp_path / "tenants" / "acme.yaml"

    def test_builds_absolute_path_with_custom_convention(self, tmp_path):
        conv = MagicMock()
        conv.resolves = "tenant"
        conv.pattern = "customers/{code}/customer.yaml"
        config_model = MagicMock()
        config_model.spec.paths = [conv]

        result = resolve_tenant_file_path(tmp_path, "acme", config_model)
        assert result == tmp_path / "customers" / "acme" / "customer.yaml"

    def test_returned_path_is_a_path_object(self, tmp_path):
        result = resolve_tenant_file_path(tmp_path, "acme")
        assert isinstance(result, Path)
