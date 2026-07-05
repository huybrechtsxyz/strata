#!/usr/bin/env python3
"""Tests for EnvironmentService.merge_envfiles() — complete section merging and provenance."""

from pathlib import Path

from strata.services.environment_service import EnvironmentService
from strata.utils.merge_provenance import MergeProvenance


def _data_dir() -> Path:
    return Path(__file__).parent.parent.parent / "data"


def _env(filename: str) -> str:
    """Return relative path string as seen from data dir (for merge_envfiles)."""
    return f"environments/{filename}"


class TestMergeEnvfilesVariablesSecrets:
    """Variables and secrets: last-wins by key."""

    def test_base_only_returns_all_vars(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml")],
            _data_dir(),
        )
        keys = {v.key for v in (model.spec.variables or [])}
        assert "APP_HOST" in keys
        assert "APP_PORT" in keys

    def test_override_wins_for_shared_key(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        var_map = {v.key: v.value for v in (model.spec.variables or [])}
        # APP_HOST overridden by prd file
        assert var_map["APP_HOST"] == "prd.internal"
        # APP_PORT only in base — still present
        assert var_map["APP_PORT"] == "8080"
        # APP_TIMEOUT only in prd — present
        assert var_map["APP_TIMEOUT"] == "30"

    def test_secret_override_wins(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        secret_map = {s.key: s.value for s in (model.spec.secrets or [])}
        assert secret_map["DB_PASSWORD"] == "prd-secret"
        # API_KEY only in base — still present
        assert secret_map["API_KEY"] == "base-api-key"

    def test_single_file_returns_expected_vars(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-override.yaml")],
            _data_dir(),
        )
        keys = {v.key for v in (model.spec.variables or [])}
        assert "APP_HOST" in keys
        assert "APP_TIMEOUT" in keys


class TestMergeEnvfilesFeatures:
    """Features: last-wins per key (not wholesale replacement)."""

    def test_base_features_preserved_unless_overridden(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        feat_map = {f.key: f.value for f in (model.spec.features or [])}
        # enable_debug overridden by prd to true
        assert feat_map["enable_debug"] is True
        # enable_metrics only in base — must still be present
        assert feat_map["enable_metrics"] is True

    def test_single_file_features_present(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml")],
            _data_dir(),
        )
        feat_map = {f.key: f.value for f in (model.spec.features or [])}
        assert feat_map["enable_debug"] is False
        assert feat_map["enable_metrics"] is True


class TestMergeEnvfilesProperties:
    """Properties and custom: shallow dict.update — later file overlays earlier."""

    def test_properties_shallow_merged(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        props = model.spec.properties or {}
        # log_level overridden by prd
        assert props["log_level"] == "warning"
        # region only in base — preserved
        assert props["region"] == "eu-fr"
        # extra_prop only in prd — present
        assert props["extra_prop"] == "prd_value"

    def test_custom_shallow_merged(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        custom = model.spec.custom or {}
        # costcenter overridden
        assert custom["costcenter"] == "prd"
        # team only in base — preserved
        assert custom["team"] == "platform"


class TestMergeEnvfilesOverrides:
    """Overrides: resource/provider last-wins by name; missing resources preserved."""

    def test_resource_override_last_wins_by_name(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        ovr = model.spec.overrides
        assert ovr is not None
        res_map = {str(r.resource): r for r in (ovr.resources or [])}
        # manager overridden by prd file
        assert res_map["manager"].count == 3
        assert res_map["manager"].configuration["vm_size"] == "Standard_D4s_v3"
        # worker only in prd — present
        assert "worker" in res_map
        assert res_map["worker"].count == 2

    def test_provider_override_last_wins_by_name(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-merge-base.yaml"), _env("environment-merge-override.yaml")],
            _data_dir(),
        )
        ovr = model.spec.overrides
        assert ovr is not None
        prov_map = {str(p.provider): p for p in (ovr.providers or [])}
        assert prov_map["cloud_provider"].configuration["datacenter"] == "FR"

    def test_no_overrides_in_single_file_without_overrides(self):
        model, _ = EnvironmentService.merge_envfiles(
            [_env("environment-standard.yaml")],
            _data_dir(),
        )
        # environment-standard.yaml has no overrides section
        assert model.spec.overrides is None


class TestMergeEnvfilesProvenance:
    """MergeProvenance records correct source files per key."""

    def test_provenance_merge_order(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        assert prov.merge_order == [base, override]

    def test_single_file_merge_order(self):
        base = _env("environment-merge-base.yaml")
        _, prov = EnvironmentService.merge_envfiles([base], _data_dir())
        assert prov.merge_order == [base]
        assert not prov.is_multi_file()

    def test_is_multi_file_true_for_two_files(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        assert prov.is_multi_file()

    def test_variable_source_tracks_winning_file(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        # APP_HOST overridden by prd
        assert prov.variable_sources["APP_HOST"] == override
        # APP_PORT only in base
        assert prov.variable_sources["APP_PORT"] == base
        # APP_TIMEOUT only in prd
        assert prov.variable_sources["APP_TIMEOUT"] == override

    def test_secret_source_tracks_winning_file(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        assert prov.secret_sources["DB_PASSWORD"] == override
        assert prov.secret_sources["API_KEY"] == base

    def test_feature_source_tracks_winning_file(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        assert prov.feature_sources["enable_debug"] == override
        assert prov.feature_sources["enable_metrics"] == base

    def test_variable_overridden_tracks_previous_sources(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        # APP_HOST was first set by base then overridden by prd
        assert prov.variable_overridden.get("APP_HOST") == [base]

    def test_unique_key_has_no_overridden_entry(self):
        base = _env("environment-merge-base.yaml")
        override = _env("environment-merge-override.yaml")
        _, prov = EnvironmentService.merge_envfiles([base, override], _data_dir())
        # APP_PORT only in base — never overridden
        assert "APP_PORT" not in prov.variable_overridden

    def test_single_file_no_overridden_entries(self):
        base = _env("environment-merge-base.yaml")
        _, prov = EnvironmentService.merge_envfiles([base], _data_dir())
        assert prov.variable_overridden == {}
        assert prov.secret_overridden == {}
        assert prov.feature_overridden == {}

    def test_returns_tuple(self):
        base = _env("environment-merge-base.yaml")
        result = EnvironmentService.merge_envfiles([base], _data_dir())
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[1], MergeProvenance)
