"""Tests for ToolsController."""

from unittest.mock import MagicMock, patch

import pytest

from xyz_platform.controllers.tools_controller import ToolsController, _BUILTIN_TYPES


def _make_integration(available=True, version="1.0.0", capabilities=None, command="tool"):
    integration = MagicMock()
    integration.is_available.return_value = available
    integration.get_version.return_value = version if available else None
    integration.get_setup_info.return_value = {
        "name": "tool",
        "command": command,
        "install_url": "https://example.com",
        "env_vars": [],
        "auth_methods": [],
        "yaml_example": None,
    }
    integration.CAPABILITIES = capabilities or []
    return integration


class TestToolsControllerStatus:
    def test_returns_row_per_builtin_type(self):
        ctrl = ToolsController()
        mock_integration = _make_integration()
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            return_value=mock_integration,
        ):
            success, rows, errors = ctrl.status()

        assert success is True
        assert len(rows) == len(_BUILTIN_TYPES)
        assert errors == []

    def test_row_shape(self):
        ctrl = ToolsController()
        mock_integration = _make_integration(available=True, version="2.0.0", command="git")
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            return_value=mock_integration,
        ):
            _, rows, _ = ctrl.status()

        row = rows[0]
        assert "name" in row
        assert "available" in row
        assert "version" in row
        assert "capabilities" in row
        assert "command" in row

    def test_unavailable_integration_returns_false(self):
        ctrl = ToolsController()
        mock_integration = _make_integration(available=False)
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            return_value=mock_integration,
        ):
            _, rows, _ = ctrl.status()

        assert all(r["available"] is False for r in rows)
        assert all(r["version"] is None for r in rows)

    def test_factory_exception_returns_unavailable_row(self):
        ctrl = ToolsController()
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            side_effect=RuntimeError("load failed"),
        ):
            success, rows, errors = ctrl.status()

        assert success is True  # status() always returns True even with errors
        assert len(rows) == len(_BUILTIN_TYPES)
        for row in rows:
            assert row["available"] is False


class TestToolsControllerCheck:
    def test_check_known_integration_available(self):
        ctrl = ToolsController()
        mock_integration = _make_integration(available=True, version="2.40.0")
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            return_value=mock_integration,
        ):
            success, detail, errors = ctrl.check("git")

        assert success is True
        assert errors == []
        assert "name" in detail
        assert "available" in detail
        assert "install_url" in detail

    def test_check_known_integration_unavailable(self):
        ctrl = ToolsController()
        mock_integration = _make_integration(available=False)
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            return_value=mock_integration,
        ):
            success, detail, errors = ctrl.check("git")

        assert success is False
        assert len(errors) > 0

    def test_check_unknown_integration(self):
        ctrl = ToolsController()
        success, detail, errors = ctrl.check("xyz_totally_unknown")

        assert success is False
        assert detail == {}
        assert any("Unknown" in e for e in errors)

    def test_check_factory_exception(self):
        ctrl = ToolsController()
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            side_effect=ValueError("not registered"),
        ):
            success, detail, errors = ctrl.check("git")

        assert success is False
        assert any("Failed to load" in e for e in errors)

    def test_check_detail_includes_setup_info(self):
        ctrl = ToolsController()
        mock_integration = _make_integration(available=True, version="1.0.0")
        with patch(
            "xyz_platform.controllers.tools_controller.IntegrationFactory.create_by_type",
            return_value=mock_integration,
        ):
            _, detail, _ = ctrl.check("git")

        assert "install_url" in detail
        assert "env_vars" in detail
        assert "auth_methods" in detail
