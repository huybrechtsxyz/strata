"""Tests for Gap 4 — inputs_from (output passing between provisioners).

Covers:
- ProvisionerInputMappingModel validation (mapping/prefix exclusivity)
- WorkspaceSpecModel validation (references, self-references, cycles)
- apply_input_mapping utility (mapping, prefix, select, errors)
- collect_inputs_from_keys utility
"""

import pytest
from pydantic import ValidationError

from strata.models.common_models import SourceModel
from strata.models.workspace_model import (
    ProvisionerInputMappingModel,
    WorkspaceComponentModel,
    WorkspaceIacModel,
    WorkspaceProviderModel,
    WorkspaceResourceModel,
    WorkspaceSpecModel,
    WorkspaceTopologyModel,
)
from strata.utils.resolved_values import apply_input_mapping, collect_inputs_from_keys

# ---------------------------------------------------------------------------
# Helper to build minimal workspace specs for validation tests
# ---------------------------------------------------------------------------


def _spec(provisioners, **kwargs):
    """Build a WorkspaceSpecModel with the minimum required fields."""
    return WorkspaceSpecModel(
        providers=[WorkspaceProviderModel(name="azure", file="providers/azure.yaml")],
        provisioners=provisioners,
        topology=[
            WorkspaceTopologyModel(
                name="main",
                provider="azure",
                provisioner=str(provisioners[0].name),
                type="kubernetes",
                components=[WorkspaceComponentModel(resource="app")],
            )
        ],
        resources=[WorkspaceResourceModel(name="app", file="resources/app.yaml")],
        **kwargs,
    )


def _prov(name, inputs_from=None):
    """Build a minimal terraform provisioner."""
    return WorkspaceIacModel(
        name=name,
        provisioner="terraform",
        source=SourceModel(source_path="terraform", repository="my_repo"),
        inputs_from=inputs_from,
    )


# ---------------------------------------------------------------------------
# ProvisionerInputMappingModel validation
# ---------------------------------------------------------------------------


class TestProvisionerInputMappingModel:
    def test_mapping_only(self):
        m = ProvisionerInputMappingModel(
            provisioner="upstream",
            mapping={"vnet_id": "platform_vnet_id"},
        )
        assert m.mapping == {"vnet_id": "platform_vnet_id"}
        assert m.prefix is None

    def test_prefix_only(self):
        m = ProvisionerInputMappingModel(provisioner="upstream", prefix="baseline_")
        assert m.prefix == "baseline_"
        assert m.mapping is None

    def test_select_only(self):
        m = ProvisionerInputMappingModel(
            provisioner="upstream",
            select=["vnet_id", "subnet_ids"],
        )
        assert m.select == ["vnet_id", "subnet_ids"]

    def test_mapping_and_prefix_raises(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            ProvisionerInputMappingModel(
                provisioner="upstream",
                mapping={"a": "b"},
                prefix="pre_",
            )

    def test_select_with_prefix_valid(self):
        m = ProvisionerInputMappingModel(
            provisioner="upstream",
            select=["vnet_id"],
            prefix="baseline_",
        )
        assert m.select == ["vnet_id"]
        assert m.prefix == "baseline_"

    def test_no_mapping_no_prefix_valid(self):
        m = ProvisionerInputMappingModel(provisioner="upstream")
        assert m.mapping is None
        assert m.prefix is None
        assert m.select is None


# ---------------------------------------------------------------------------
# WorkspaceSpecModel — inputs_from validation
# ---------------------------------------------------------------------------


class TestInputsFromValidation:
    def test_valid_reference(self):
        """inputs_from referencing an existing provisioner passes."""
        p1 = _prov("baseline")
        p2 = _prov(
            "vct",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="baseline"),
            ],
        )
        spec = _spec([p1, p2])
        assert spec is not None

    def test_unknown_provisioner_raises(self):
        p1 = _prov("baseline")
        p2 = _prov(
            "vct",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="nonexistent"),
            ],
        )
        with pytest.raises(ValidationError, match="unknown provisioner"):
            _spec([p1, p2])

    def test_self_reference_raises(self):
        p1 = _prov(
            "baseline",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="baseline"),
            ],
        )
        with pytest.raises(ValidationError, match="cannot reference itself"):
            _spec([p1])

    def test_circular_dependency_raises(self):
        p1 = _prov(
            "a",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="b"),
            ],
        )
        p2 = _prov(
            "b",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="a"),
            ],
        )
        with pytest.raises(ValidationError, match="Circular dependency"):
            _spec([p1, p2])

    def test_no_inputs_from_valid(self):
        """Provisioners without inputs_from pass validation."""
        spec = _spec([_prov("baseline"), _prov("vct")])
        assert spec is not None

    def test_multiple_inputs_from_valid(self):
        p1 = _prov("baseline")
        p2 = _prov("networking")
        p3 = _prov(
            "vct",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="baseline"),
                ProvisionerInputMappingModel(provisioner="networking"),
            ],
        )
        spec = _spec([p1, p2, p3])
        assert spec is not None

    def test_chain_a_to_b_to_c_valid(self):
        """A → B → C (linear chain) is valid."""
        p1 = _prov("a")
        p2 = _prov(
            "b",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="a"),
            ],
        )
        p3 = _prov(
            "c",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="b"),
            ],
        )
        spec = _spec([p1, p2, p3])
        assert spec is not None

    def test_three_node_cycle_raises(self):
        p1 = _prov(
            "a",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="c"),
            ],
        )
        p2 = _prov(
            "b",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="a"),
            ],
        )
        p3 = _prov(
            "c",
            inputs_from=[
                ProvisionerInputMappingModel(provisioner="b"),
            ],
        )
        with pytest.raises(ValidationError, match="Circular dependency"):
            _spec([p1, p2, p3])


# ---------------------------------------------------------------------------
# apply_input_mapping
# ---------------------------------------------------------------------------


class TestApplyInputMapping:
    def test_passthrough(self):
        outputs = {"vnet_id": "123", "cluster_id": "456"}
        result = apply_input_mapping(outputs)
        assert result == outputs

    def test_mapping(self):
        outputs = {"vnet_id": "123", "cluster_id": "456"}
        result = apply_input_mapping(outputs, mapping={"vnet_id": "platform_vnet_id", "cluster_id": "aks_id"})
        assert result == {"platform_vnet_id": "123", "aks_id": "456"}

    def test_mapping_skips_missing_upstream_keys(self):
        outputs = {"vnet_id": "123"}
        result = apply_input_mapping(outputs, mapping={"vnet_id": "vnet", "missing": "gone"})
        assert result == {"vnet": "123"}

    def test_prefix(self):
        outputs = {"vnet_id": "123", "cluster_id": "456"}
        result = apply_input_mapping(outputs, prefix="baseline_")
        assert result == {"baseline_vnet_id": "123", "baseline_cluster_id": "456"}

    def test_select(self):
        outputs = {"vnet_id": "123", "cluster_id": "456", "extra": "789"}
        result = apply_input_mapping(outputs, select=["vnet_id", "cluster_id"])
        assert result == {"vnet_id": "123", "cluster_id": "456"}

    def test_select_missing_raises(self):
        outputs = {"vnet_id": "123"}
        with pytest.raises(ValueError, match="Expected outputs not found"):
            apply_input_mapping(outputs, select=["vnet_id", "missing_key"])

    def test_select_with_prefix(self):
        outputs = {"vnet_id": "123", "cluster_id": "456", "extra": "789"}
        result = apply_input_mapping(outputs, select=["vnet_id"], prefix="baseline_")
        assert result == {"baseline_vnet_id": "123"}

    def test_select_with_mapping(self):
        outputs = {"vnet_id": "123", "extra": "789"}
        result = apply_input_mapping(
            outputs,
            select=["vnet_id"],
            mapping={"vnet_id": "my_vnet"},
        )
        assert result == {"my_vnet": "123"}

    def test_empty_outputs(self):
        result = apply_input_mapping({})
        assert result == {}


# ---------------------------------------------------------------------------
# collect_inputs_from_keys
# ---------------------------------------------------------------------------


class TestCollectInputsFromKeys:
    def test_mapping_returns_downstream_names(self):
        inp = ProvisionerInputMappingModel(
            provisioner="upstream",
            mapping={"vnet_id": "platform_vnet_id", "cluster_id": "aks_id"},
        )
        keys = collect_inputs_from_keys([inp])
        assert keys == {"platform_vnet_id", "aks_id"}

    def test_select_returns_selected_names(self):
        inp = ProvisionerInputMappingModel(
            provisioner="upstream",
            select=["vnet_id", "cluster_id"],
        )
        keys = collect_inputs_from_keys([inp])
        assert keys == {"vnet_id", "cluster_id"}

    def test_select_with_prefix(self):
        inp = ProvisionerInputMappingModel(
            provisioner="upstream",
            select=["vnet_id"],
            prefix="baseline_",
        )
        keys = collect_inputs_from_keys([inp])
        assert keys == {"baseline_vnet_id"}

    def test_no_mapping_no_select_returns_empty(self):
        """Without mapping or select, keys are unknown at build time."""
        inp = ProvisionerInputMappingModel(provisioner="upstream")
        keys = collect_inputs_from_keys([inp])
        assert keys == set()

    def test_none_returns_empty(self):
        assert collect_inputs_from_keys(None) == set()

    def test_multiple_inputs(self):
        inp1 = ProvisionerInputMappingModel(
            provisioner="a",
            mapping={"x": "ax"},
        )
        inp2 = ProvisionerInputMappingModel(
            provisioner="b",
            select=["y"],
        )
        keys = collect_inputs_from_keys([inp1, inp2])
        assert keys == {"ax", "y"}
