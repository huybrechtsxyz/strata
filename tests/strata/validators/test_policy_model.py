"""Tests for PolicyModel validation."""

import pytest
from pydantic import ValidationError

from strata.models.policy_model import PolicyModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(**kwargs):
    """Build a minimal valid PolicyModel, merging caller overrides."""
    defaults = {
        "name": "zone_check",
        "type": "tenant_zone",
        "phase": "plan",
    }
    defaults.update(kwargs)
    return PolicyModel(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPolicyModel:
    def test_valid_minimal(self):
        """name, type, and phase only — all other fields get defaults."""
        model = _make_policy()

        assert model.name == "zone_check"
        assert model.type == "tenant_zone"
        assert model.phase == "plan"

    def test_valid_with_enforcement(self):
        """All fields populated — enforcement, description, configuration, enabled."""
        model = _make_policy(
            enforcement="warn",
            description="Ensure resources are in tenant-allowed zones",
            configuration={"check_all": True},
            enabled=True,
        )

        assert model.enforcement == "warn"
        assert model.description == "Ensure resources are in tenant-allowed zones"
        assert model.configuration == {"check_all": True}
        assert model.enabled is True

    def test_defaults(self):
        """enforcement defaults to 'deny', enabled defaults to True."""
        model = _make_policy()

        assert model.enforcement == "deny"
        assert model.enabled is True
        assert model.description is None
        assert model.configuration is None

    def test_invalid_name(self):
        """PlatformName rejects uppercase letters and special characters."""
        with pytest.raises(ValidationError):
            _make_policy(name="my-POLICY!")

    def test_invalid_phase_not_validated(self):
        """phase is plain str — no enum constraint at the model level."""
        model = _make_policy(phase="custom_phase_xyz")

        assert model.phase == "custom_phase_xyz"

    def test_configuration_dict(self):
        """configuration accepts arbitrary dict — used to pass policy parameters."""
        model = _make_policy(configuration={"required": ["env", "owner"]})

        assert model.configuration == {"required": ["env", "owner"]}
        assert model.configuration["required"] == ["env", "owner"]

    def test_disabled_policy(self):
        """enabled=False is valid and round-trips correctly."""
        model = _make_policy(enabled=False)

        assert model.enabled is False
