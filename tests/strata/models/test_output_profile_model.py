"""Tests for OutputProfileModel, OutputFileModel, OutputFileSourceModel."""

import pytest
from pydantic import ValidationError

from strata.models.workspace_model import (
    OutputFileModel,
    OutputFileSourceModel,
    OutputProfileModel,
)


class TestOutputProfileModel:
    def test_defaults_to_strata_format(self):
        p = OutputProfileModel()
        assert p.format == "strata"
        assert p.emits is None
        assert p.files is None
        assert p.script is None

    def test_should_emit_strata_emits_all(self):
        p = OutputProfileModel(format="strata")
        for cat in ["workspace", "providers", "features", "variables", "properties", "custom", "tenant"]:
            assert p.should_emit(cat) is True

    def test_should_emit_strata_never_emits_secrets(self):
        p = OutputProfileModel(format="strata")
        assert p.should_emit("secrets") is False

    def test_should_emit_custom_emits_nothing_by_default(self):
        p = OutputProfileModel(format="custom")
        assert p.should_emit("workspace") is False
        assert p.should_emit("features") is False

    def test_should_emit_custom_with_explicit_emits(self):
        p = OutputProfileModel(format="custom", emits=["features", "variables"])
        assert p.should_emit("features") is True
        assert p.should_emit("variables") is True
        assert p.should_emit("workspace") is False

    def test_should_emit_none_format(self):
        p = OutputProfileModel(format="none")
        assert p.should_emit("workspace") is False
        assert p.should_emit("features") is False

    def test_emits_overrides_strata_defaults(self):
        p = OutputProfileModel(format="strata", emits=["features"])
        assert p.should_emit("features") is True
        # Other categories excluded because emits is set
        assert p.should_emit("workspace") is False

    def test_format_script_requires_script_path(self):
        with pytest.raises(ValidationError, match="'script' path is required"):
            OutputProfileModel(format="script")

    def test_format_script_with_path_is_valid(self):
        p = OutputProfileModel(format="script", script="scripts/build.py")
        assert p.script == "scripts/build.py"

    def test_script_field_only_valid_for_script_format(self):
        with pytest.raises(ValidationError, match="'script' is only valid when format is 'script'"):
            OutputProfileModel(format="custom", script="scripts/build.py")

    def test_format_none_rejects_emits(self):
        with pytest.raises(ValidationError, match="no effect when format is 'none'"):
            OutputProfileModel(format="none", emits=["features"])

    def test_format_none_rejects_files(self):
        with pytest.raises(ValidationError, match="no effect when format is 'none'"):
            OutputProfileModel(format="none", files=[OutputFileModel(name="x.json", source="properties", key="k")])


class TestOutputFileModel:
    def test_source_mode_minimal(self):
        f = OutputFileModel(name="aks.auto.tfvars.json", variable="aks_config", source="properties", key="aks_config")
        assert f.source == "properties"
        assert f.key == "aks_config"
        assert f.script is None

    def test_source_mode_without_variable(self):
        f = OutputFileModel(name="flat.auto.tfvars.json", type="flat", source="properties", key="aks_config")
        assert f.variable is None

    def test_script_mode(self):
        f = OutputFileModel(name="env_info.auto.tfvars.json", type="script", script="@repo/scripts/build.py")
        assert f.script == "@repo/scripts/build.py"

    def test_script_type_requires_script_path(self):
        with pytest.raises(ValidationError, match="'script' path is required"):
            OutputFileModel(name="x.json", type="script")

    def test_source_requires_key(self):
        with pytest.raises(ValidationError, match="'key' is required when 'source' is set"):
            OutputFileModel(name="x.json", source="properties")

    def test_sources_and_source_mutually_exclusive(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            OutputFileModel(
                name="x.json",
                source="properties",
                key="k",
                sources=[OutputFileSourceModel(variable="v", source="properties", key="k")],
            )

    def test_sources_and_script_mutually_exclusive(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            OutputFileModel(
                name="x.json",
                type="script",
                script="s.py",
                sources=[OutputFileSourceModel(variable="v", source="properties", key="k")],
            )

    def test_multi_source_mode(self):
        f = OutputFileModel(
            name="platform.json",
            sources=[
                OutputFileSourceModel(variable="aks", source="properties", key="aks_config"),
                OutputFileSourceModel(variable="dns", source="properties", key="dns_config"),
            ],
        )
        assert len(f.sources) == 2
        assert f.sources[0].variable == "aks"

    def test_sources_entry_requires_all_fields(self):
        with pytest.raises(ValidationError):
            OutputFileModel(
                name="x.json",
                sources=[OutputFileSourceModel(variable="v", source="properties", key="")],
            )


class TestOutputFileSourceModel:
    def test_valid_entry(self):
        e = OutputFileSourceModel(variable="aks_config", source="properties", key="aks_config")
        assert e.type == "object"

    def test_list_type(self):
        e = OutputFileSourceModel(variable="vms", source="properties", key="virtual_machines", type="list")
        assert e.type == "list"
