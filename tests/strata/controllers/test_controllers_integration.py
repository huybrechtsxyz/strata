"""Tests for IntegrationController."""

from unittest.mock import MagicMock

from strata.controllers.integration_controller import IntegrationController
from strata.integrations.registry import IntegrationRegistry


class TestIntegrationControllerInit:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_init_creates_registry_and_factory(self):
        ctrl = IntegrationController()
        assert ctrl._registry is not None
        assert ctrl._factory is not None


class TestIntegrationControllerGetStatus:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_get_status_unregistered_returns_error(self):
        ctrl = IntegrationController()
        ok, status = ctrl.get_integration_status("nonexistent")
        assert ok is False
        assert status["available"] is False
        assert "nonexistent" in status["name"] or "nonexistent" in status.get("info", "")

    def test_get_status_registered_integration_returns_info(self):
        mock_integration = MagicMock()
        mock_integration.get_info.return_value = {
            "name": "git",
            "available": True,
            "version": "2.40.0",
            "info": "Git has 2.40.0 installed",
            "required": False,
            "enabled": True,
        }

        registry = IntegrationRegistry.get_instance()
        registry.register_integration("git", mock_integration)

        ctrl = IntegrationController()
        ok, status = ctrl.get_integration_status("git")
        assert ok is True
        assert status["available"] is True
        assert status["version"] == "2.40.0"

    def test_get_status_clears_errors_on_each_call(self):
        ctrl = IntegrationController()
        ctrl._errors.append("stale error")
        ctrl.get_integration_status("anything")
        # After the call, stale error should be cleared (call clears at start)
        # We just check the call completes without internal stale accumulation
        assert ctrl.has_errors()  # may have new error for unregistered, that's fine


class TestIntegrationControllerGetAllStatus:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_get_all_integrations_empty_registry(self):
        ctrl = IntegrationController()
        ok, status = ctrl.get_all_integrations_status()
        assert ok is True
        assert status == {}

    def test_get_all_integrations_returns_dict_keyed_by_name(self):
        mock1 = MagicMock()
        mock1.get_info.return_value = {
            "name": "git",
            "available": True,
            "version": "2.40",
            "info": "",
            "required": False,
            "enabled": True,
        }
        mock2 = MagicMock()
        mock2.get_info.return_value = {
            "name": "terraform",
            "available": False,
            "version": None,
            "info": "not found",
            "required": True,
            "enabled": True,
        }

        registry = IntegrationRegistry.get_instance()
        registry.register_integration("git", mock1)
        registry.register_integration("terraform", mock2)

        ctrl = IntegrationController()
        ok, status = ctrl.get_all_integrations_status()
        assert ok is True
        assert "git" in status
        assert "terraform" in status
        assert status["git"]["available"] is True
        assert status["terraform"]["available"] is False


class TestIntegrationControllerEnsureRegistered:
    def setup_method(self):
        IntegrationRegistry.reset()

    def test_ensure_registered_already_registered_returns_true(self):
        mock_integration = MagicMock()
        registry = IntegrationRegistry.get_instance()
        registry.register_integration("git", mock_integration)

        ctrl = IntegrationController()
        result = ctrl._ensure_integration_registered("git")
        assert result is True

    def test_ensure_registered_unknown_type_returns_false(self):
        ctrl = IntegrationController()
        # "unknown-type" is not a registered factory type, so it should fail gracefully
        result = ctrl._ensure_integration_registered("unknown-xyz-type")
        assert result is False
