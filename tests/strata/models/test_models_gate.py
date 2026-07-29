"""Unit tests for gate models — DeploymentGateModel, ApproverRef, GateWhenConditionsModel.

Covers the ADR-0059 unified schema: gates living on DeploymentSpecModel (not
EnvironmentSpecModel), the `name`/`mode`/`scope` fields, and the richer
`Dict[str, ApproverRef]` approver shape absorbed from the old spec.approvals.
"""

import pytest
from pydantic import ValidationError

from strata.models.deployment_model import DeploymentSpecModel, DeploymentStageModel
from strata.models.gate_model import (
    ApproverRef,
    ApproverType,
    DeploymentGateModel,
    GateWhenConditionsModel,
)


class TestApproverRef:
    def test_valid_github_team(self):
        ref = ApproverRef(type="github-team", value="org/platform-team")
        assert ref.type == ApproverType.GITHUB_TEAM
        assert ref.value == "org/platform-team"

    def test_valid_user(self):
        ref = ApproverRef(type="user", value="devops@example.com")
        assert ref.type == ApproverType.USER

    def test_valid_ado_group(self):
        ref = ApproverRef(type="ado-group", value="Platform-Approvers")
        assert ref.type == ApproverType.ADO_GROUP

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ApproverRef(type="slack-channel", value="#ops")  # type: ignore[arg-type]


class TestGateWhenConditionsModel:
    def test_all_fields_optional(self):
        cond = GateWhenConditionsModel()
        assert cond.cost_delta_monthly is None
        assert cond.cve_critical is None
        assert cond.time_utc is None

    def test_partial_conditions(self):
        cond = GateWhenConditionsModel(cost_delta_monthly=">= 1000")
        assert cond.cost_delta_monthly == ">= 1000"
        assert cond.cve_critical is None


class TestDeploymentGateModel:
    def test_minimal_gate(self):
        gate = DeploymentGateModel(name="prod-approval", type="approval")
        assert gate.name == "prod-approval"
        assert gate.type == "approval"
        assert gate.mode == "enforce"  # default
        assert gate.scope == "all"  # default
        assert gate.when == "always"  # default
        assert gate.approvers is None
        assert gate.min_approvals == 1
        assert gate.timeout_minutes is None
        assert gate.auto_resolve is False

    def test_mode_declare(self):
        gate = DeploymentGateModel(name="audit-only", type="approval", mode="declare")
        assert gate.mode == "declare"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentGateModel(name="g", type="approval", mode="both")  # type: ignore[arg-type]

    def test_scope_as_stage_list(self):
        gate = DeploymentGateModel(name="g", type="approval", scope=["production"])
        assert gate.scope == ["production"]

    def test_scope_all_default(self):
        gate = DeploymentGateModel(name="g", type="cost_review")
        assert gate.scope == "all"

    def test_approvers_dict_shape(self):
        gate = DeploymentGateModel(
            name="g",
            type="approval",
            approvers={
                "platform-team": {"type": "github-team", "value": "org/platform-team"},
                "devops-lead": {"type": "user", "value": "devops@example.com"},
            },
        )
        assert gate.approvers is not None
        assert gate.approvers["platform-team"].type == ApproverType.GITHUB_TEAM
        assert gate.approvers["devops-lead"].value == "devops@example.com"

    def test_when_conditions_object(self):
        gate = DeploymentGateModel(
            name="cost-guard",
            type="cost_review",
            when={"cost_delta_monthly": ">= 1000"},
        )
        assert isinstance(gate.when, GateWhenConditionsModel)
        assert gate.when.cost_delta_monthly == ">= 1000"

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentGateModel(name="g", type="approval", unknown_field="x")  # type: ignore[call-arg]


class TestDeploymentSpecModelGates:
    """Deployment-level spec.gates field and its validators (ADR-0059)."""

    def test_gates_optional_defaults_none(self):
        spec = DeploymentSpecModel()
        assert spec.gates is None

    def test_gates_parsed_from_list(self):
        spec = DeploymentSpecModel(
            gates=[{"name": "prod-approval", "type": "approval"}],
        )
        assert spec.gates is not None
        assert len(spec.gates) == 1
        assert spec.gates[0].name == "prod-approval"

    def test_duplicate_gate_names_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate gate names"):
            DeploymentSpecModel(
                gates=[
                    {"name": "dup", "type": "approval"},
                    {"name": "dup", "type": "cost_review"},
                ],
            )

    def test_gate_scope_referencing_unknown_stage_rejected(self):
        with pytest.raises(ValidationError, match="unknown stage"):
            DeploymentSpecModel(
                stages=[DeploymentStageModel(name="staging")],
                gates=[{"name": "g", "type": "approval", "scope": ["production"]}],
            )

    def test_gate_scope_referencing_known_stage_accepted(self):
        spec = DeploymentSpecModel(
            stages=[DeploymentStageModel(name="staging"), DeploymentStageModel(name="production")],
            gates=[{"name": "g", "type": "approval", "scope": ["production"]}],
        )
        assert spec.gates[0].scope == ["production"]

    def test_gate_scope_all_skips_stage_validation(self):
        """scope: 'all' should never trigger unknown-stage errors, even with no stages declared."""
        spec = DeploymentSpecModel(
            gates=[{"name": "g", "type": "cost_review", "scope": "all"}],
        )
        assert spec.gates[0].scope == "all"

    def test_gate_scope_without_any_stages_declared_rejected(self):
        with pytest.raises(ValidationError, match="unknown stage"):
            DeploymentSpecModel(
                gates=[{"name": "g", "type": "approval", "scope": ["production"]}],
            )
