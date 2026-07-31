"""Tests for ResourceTypeRestrictionsPolicy — allow/deny list on Terraform resource types."""

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.resource_type_restrictions_policy import ResourceTypeRestrictionsPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy(
    mode: str = "deny", types: list | None = None, actions: list | None = None
) -> "ResourceTypeRestrictionsPolicy":
    cfg = {"mode": mode, "types": types or []}
    if actions is not None:
        cfg["actions"] = actions
    return ResourceTypeRestrictionsPolicy(
        PolicyModel(
            name="test_policy",
            type="resource_type_restrictions",
            phase="plan",
            enforcement="deny",
            configuration=cfg,
        )
    )


def _context(plan_data: dict | None) -> "PolicyContext":
    return PolicyContext(phase="plan", work_path=None, plan_data=plan_data)


def _plan_with(*resource_types: str, action: str = "create") -> dict:
    return {
        "resource_changes": [
            {
                "type": rt,
                "name": "res",
                "address": f"{rt}.res",
                "change": {"actions": [action], "after": {}},
            }
            for rt in resource_types
        ]
    }


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_no_plan_data_skips(self):
        policy = _policy(types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context(None))
        assert result.passed
        assert result.details and "skipped" in result.details

    def test_no_types_configured_skips(self):
        policy = _policy(types=[])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine")))
        assert result.passed
        assert result.details and "skipped" in result.details

    def test_empty_resource_changes_passes(self):
        policy = _policy(types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context({"resource_changes": []}))
        assert result.passed

    def test_missing_resource_changes_key_passes(self):
        policy = _policy(types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context({}))
        assert result.passed


# ---------------------------------------------------------------------------
# Deny mode
# ---------------------------------------------------------------------------


class TestDenyMode:
    def test_denied_type_is_violation(self):
        policy = _policy(mode="deny", types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine")))
        assert not result.passed
        assert len(result.violations) == 1
        assert "azurerm_virtual_machine" in result.violations[0]

    def test_allowed_type_passes(self):
        policy = _policy(mode="deny", types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context(_plan_with("azurerm_kubernetes_cluster")))
        assert result.passed
        assert not result.violations

    def test_multiple_denied_types(self):
        policy = _policy(mode="deny", types=["azurerm_virtual_machine", "aws_instance"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", "aws_instance")))
        assert not result.passed
        assert len(result.violations) == 2

    def test_mixed_types_partial_violation(self):
        policy = _policy(mode="deny", types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", "azurerm_kubernetes_cluster")))
        assert not result.passed
        assert len(result.violations) == 1

    def test_default_mode_is_deny(self):
        """Omitting mode defaults to deny behaviour."""
        p = ResourceTypeRestrictionsPolicy(
            PolicyModel(
                name="p",
                type="resource_type_restrictions",
                phase="plan",
                enforcement="deny",
                configuration={"types": ["azurerm_virtual_machine"]},
            )
        )
        result = p.evaluate(_context(_plan_with("azurerm_virtual_machine")))
        assert not result.passed


# ---------------------------------------------------------------------------
# Allow mode
# ---------------------------------------------------------------------------


class TestAllowMode:
    def test_unlisted_type_is_violation(self):
        policy = _policy(mode="allow", types=["azurerm_kubernetes_cluster"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine")))
        assert not result.passed
        assert "azurerm_virtual_machine" in result.violations[0]

    def test_listed_type_passes(self):
        policy = _policy(mode="allow", types=["azurerm_kubernetes_cluster"])
        result = policy.evaluate(_context(_plan_with("azurerm_kubernetes_cluster")))
        assert result.passed
        assert not result.violations

    def test_all_unlisted_produces_one_violation_per_resource(self):
        policy = _policy(mode="allow", types=["azurerm_kubernetes_cluster"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", "aws_instance")))
        assert not result.passed
        assert len(result.violations) == 2


# ---------------------------------------------------------------------------
# Action filtering
# ---------------------------------------------------------------------------


class TestActionFiltering:
    def test_delete_action_ignored_by_default(self):
        policy = _policy(mode="deny", types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", action="delete")))
        assert result.passed

    def test_update_action_checked_by_default(self):
        policy = _policy(mode="deny", types=["azurerm_virtual_machine"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", action="update")))
        assert not result.passed

    def test_custom_actions_respected(self):
        """When actions=[delete], only delete changes trigger the check."""
        policy = _policy(mode="deny", types=["azurerm_virtual_machine"], actions=["delete"])
        result = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", action="create")))
        assert result.passed  # create not in custom actions list

        result2 = policy.evaluate(_context(_plan_with("azurerm_virtual_machine", action="delete")))
        assert not result2.passed  # delete is in the list


# ---------------------------------------------------------------------------
# Engine registration
# ---------------------------------------------------------------------------


class TestEngineRegistration:
    def test_engine_creates_policy_by_type_name(self):
        from strata.validators.policies.policy_engine import PolicyEngine

        models = [
            PolicyModel(
                name="rtr",
                type="resource_type_restrictions",
                phase="plan",
                enforcement="deny",
                configuration={"mode": "deny", "types": ["azurerm_virtual_machine"]},
            )
        ]
        engine = PolicyEngine(models)
        results = engine.evaluate(
            "plan",
            PolicyContext(phase="plan", work_path=None, plan_data=_plan_with("azurerm_virtual_machine")),
        )
        assert len(results) == 1
        assert not results[0].passed
