"""Cross-schema `enabled` cleanup — ADR-0083 Phase 7 (D8).

Three changes, verified here:

1. `resources[].condition` and its environment override are **removed**. They were
   inert (copied by the override merge, read by nothing) and redundant — an
   environment can already switch a resource off by overriding `enabled`. A
   deprecation shim drops the key with a warning rather than hard-failing an
   upgrade, mirroring ADR-0078's `references`.
2. `resources[].enabled` now actually excludes a resource from the platform
   artifact, and therefore from every provisioner that consumes it.
3. `modules[].enabled` does the same for a resource's modules — previously it was
   read in exactly one place, and only to decide which modules counted when
   validating the "exactly one main slot" rule.
"""

import warnings
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from strata.builders.platform_builder import collect_disabled_names
from strata.models.environment_model import EnvironmentResourceOverrideModel
from strata.models.workspace_model import WorkspaceResourceModel


class TestConditionRemovedFromWorkspaceResource:
    def test_condition_is_no_longer_a_field(self):
        assert "condition" not in WorkspaceResourceModel.model_fields

    def test_condition_is_dropped_with_a_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="'condition' has been removed"):
            model = WorkspaceResourceModel(name="web", file="r.yaml", condition="{{ environment }} == production")

        assert not hasattr(model, "condition")

    def test_warning_names_the_resource_and_points_at_enabled(self):
        with pytest.warns(DeprecationWarning) as caught:
            WorkspaceResourceModel(name="web", file="r.yaml", condition="anything")

        message = str(caught[0].message)
        assert "'web'" in message
        assert "enabled" in message

    def test_upgrade_does_not_hard_fail_despite_extra_forbid(self):
        """The whole point of the shim: `extra=forbid` would otherwise break upgrades."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = WorkspaceResourceModel(name="web", file="r.yaml", condition="x")

        assert model.name == "web"

    def test_references_shim_is_gone_but_condition_remains(self):
        """The two shims are not interchangeable, despite being introduced together.

        ADR-0078's `references` shipped a warning in v1.10.0 and was removed after
        that cycle. ADR-0083's `condition` was still an accepted, documented field in
        v1.10.0 — it has not yet served a single release as a warning, so removing it
        at the same time would turn a valid file into a hard failure with no notice.
        """
        with pytest.raises(ValidationError):
            WorkspaceResourceModel(name="web", file="r.yaml", references={})

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert WorkspaceResourceModel(name="web", file="r.yaml", condition="x").name == "web"

    def test_enabled_survives_and_still_defaults_to_true(self):
        assert WorkspaceResourceModel(name="web", file="r.yaml").enabled is True
        assert WorkspaceResourceModel(name="web", file="r.yaml", enabled=False).enabled is False


class TestConditionRemovedFromEnvironmentOverride:
    def test_condition_is_no_longer_a_field(self):
        assert "condition" not in EnvironmentResourceOverrideModel.model_fields

    def test_condition_is_dropped_with_a_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="'condition' has been removed"):
            model = EnvironmentResourceOverrideModel(resource="web", condition="x")

        assert not hasattr(model, "condition")

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
