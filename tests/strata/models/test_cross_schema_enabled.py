"""Cross-schema `enabled` cleanup — ADR-0083 Phase 7 (D8).

Three changes, verified here:

1. `resources[].condition` and its environment override are **removed**. They were
   inert (copied by the override merge, read by nothing) and redundant — an
   environment can already switch a resource off by overriding `enabled`. A
   deprecation shim carried the key through one release with a warning; it has
   since been removed, so `extra="forbid"` now rejects the key outright.
2. `resources[].enabled` now actually excludes a resource from the platform
   artifact, and therefore from every provisioner that consumes it.
3. `modules[].enabled` does the same for a resource's modules — previously it was
   read in exactly one place, and only to decide which modules counted when
   validating the "exactly one main slot" rule.
"""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from strata.builders.platform_builder import collect_disabled_names
from strata.models.environment_model import EnvironmentResourceOverrideModel
from strata.models.workspace_model import WorkspaceResourceModel


class TestConditionRemovedFromWorkspaceResource:
    def test_condition_is_no_longer_a_field(self):
        assert "condition" not in WorkspaceResourceModel.model_fields

    def test_condition_is_now_rejected(self):
        """The deprecation shim served its release and has been removed."""
        with pytest.raises(ValidationError):
            WorkspaceResourceModel(name="web", file="r.yaml", condition="{{ environment }} == production")

    def test_rejection_names_the_offending_key(self):
        """`extra="forbid"` reports the field, which is what makes the error
        actionable without the shim's bespoke guidance message."""
        with pytest.raises(ValidationError) as exc:
            WorkspaceResourceModel(name="web", file="r.yaml", condition="anything")

        assert "condition" in str(exc.value)

    def test_references_is_also_rejected(self):
        """Both ADR-0078's `references` and ADR-0083's `condition` shims are gone."""
        with pytest.raises(ValidationError):
            WorkspaceResourceModel(name="web", file="r.yaml", references={})

    def test_enabled_survives_and_still_defaults_to_true(self):
        assert WorkspaceResourceModel(name="web", file="r.yaml").enabled is True
        assert WorkspaceResourceModel(name="web", file="r.yaml", enabled=False).enabled is False


class TestConditionRemovedFromEnvironmentOverride:
    def test_condition_is_no_longer_a_field(self):
        assert "condition" not in EnvironmentResourceOverrideModel.model_fields

    def test_condition_is_now_rejected(self):
        with pytest.raises(ValidationError):
            EnvironmentResourceOverrideModel(resource="web", condition="x")

    def test_enabled_override_survives(self):
        assert EnvironmentResourceOverrideModel(resource="web", enabled=False).enabled is False


def _resource_ref(name: str, enabled: bool = True, modules=None) -> MagicMock:
    ref = MagicMock()
    ref.name = name
    ref.enabled = enabled
    ref.modules = modules or []
    return ref


def _module_ref(name: str, enabled: bool = True) -> MagicMock:
    ref = MagicMock()
    ref.name = name
    ref.enabled = enabled
    return ref


class TestCollectDisabledNames:
    """The single choke point that makes `enabled` real for resources and modules.

    A disabled entry never enters the platform artifact, so every downstream
    provisioner excludes it without needing its own check.
    """

    def test_nothing_disabled_by_default(self):
        refs = [_resource_ref("web", modules=[_module_ref("app")])]

        assert collect_disabled_names(refs) == (set(), set())

    def test_none_input_is_tolerated(self):
        assert collect_disabled_names(None) == (set(), set())

    def test_empty_input_is_tolerated(self):
        assert collect_disabled_names([]) == (set(), set())

    def test_disabled_resource_is_collected(self):
        refs = [_resource_ref("web"), _resource_ref("db", enabled=False)]

        disabled_resources, _ = collect_disabled_names(refs)

        assert disabled_resources == {"db"}

    def test_disabled_module_is_collected_with_a_compound_key(self):
        refs = [_resource_ref("web", modules=[_module_ref("app", enabled=False)])]

        _, disabled_modules = collect_disabled_names(refs)

        assert disabled_modules == {"web:app"}

    def test_key_shape_matches_get_module_services(self):
        """WorkspaceService keys modules as '<resource>:<module>' — must match."""
        refs = [_resource_ref("web", modules=[_module_ref("app", enabled=False)])]

        _, disabled_modules = collect_disabled_names(refs)

        assert disabled_modules == {"web:app"}, "key format is load-bearing for the filter"

    def test_same_module_can_differ_per_resource(self):
        refs = [
            _resource_ref("web", modules=[_module_ref("agent")]),
            _resource_ref("db", modules=[_module_ref("agent", enabled=False)]),
        ]

        _, disabled_modules = collect_disabled_names(refs)

        assert disabled_modules == {"db:agent"}, "only the db reference is off"

    def test_disabled_resource_does_not_implicitly_disable_its_modules(self):
        """The resource filter already excludes it; module keys stay independent."""
        refs = [_resource_ref("db", enabled=False, modules=[_module_ref("app")])]

        disabled_resources, disabled_modules = collect_disabled_names(refs)

        assert disabled_resources == {"db"}
        assert disabled_modules == set()

    def test_resource_without_modules_attribute_is_tolerated(self):
        ref = MagicMock(spec=["name", "enabled"])
        ref.name = "web"
        ref.enabled = True

        assert collect_disabled_names([ref]) == (set(), set())

    def test_collects_across_many_resources(self):
        refs = [
            _resource_ref("a", modules=[_module_ref("m1"), _module_ref("m2", enabled=False)]),
            _resource_ref("b", enabled=False),
            _resource_ref("c", modules=[_module_ref("m3", enabled=False)]),
        ]

        disabled_resources, disabled_modules = collect_disabled_names(refs)

        assert disabled_resources == {"b"}
        assert disabled_modules == {"a:m2", "c:m3"}
