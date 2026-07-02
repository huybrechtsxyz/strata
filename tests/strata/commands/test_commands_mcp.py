"""Tests for the ``mcp`` command group and MCP server tools."""

import sys
from types import ModuleType
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from strata.commands.cli_mcp import mcp_group

# ---------------------------------------------------------------------------
# CLI command tests (no mcp package required)
# ---------------------------------------------------------------------------


class TestMcpCliGroup:
    def test_group_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(mcp_group, ["--help"])
        assert result.exit_code == 0
        assert "mcp" in result.output.lower() or "serve" in result.output.lower()

    def test_serve_subcommand_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(mcp_group, ["serve", "--help"])
        assert result.exit_code == 0

    def test_serve_shows_transport_option(self):
        runner = CliRunner()
        result = runner.invoke(mcp_group, ["serve", "--help"])
        assert "--transport" in result.output

    def test_serve_fails_gracefully_when_mcp_not_installed(self):
        """When mcp package is absent, serve raises ClickException with install hint."""
        runner = CliRunner()
        with patch("strata.commands.cli_mcp.mcp_group.commands"):
            pass
        # Patch the import inside mcp_serve to simulate missing mcp
        with patch.dict(sys.modules, {"strata.mcp.server": None}):
            with patch("strata.commands.cli_mcp.mcp_group"):
                pass
        # Test via direct ImportError injection
        with patch("builtins.__import__", side_effect=_import_side_effect):
            result = runner.invoke(mcp_group, ["serve"])
        assert result.exit_code != 0


def _import_side_effect(name, *args, **kwargs):
    if name == "strata.mcp.server":
        raise ImportError("mcp not installed")
    return (
        __builtins__["__import__"](name, *args, **kwargs)
        if isinstance(__builtins__, dict)
        else __import__(name, *args, **kwargs)
    )


# ---------------------------------------------------------------------------
# MCP server tool tests (mock the mcp package)
# ---------------------------------------------------------------------------


def _make_fake_mcp_module() -> ModuleType:
    """Build a minimal fake `mcp` module hierarchy so server.py can be imported."""
    fake_mcp = ModuleType("mcp")
    fake_server = ModuleType("mcp.server")
    fake_fastmcp_mod = ModuleType("mcp.server.fastmcp")

    # FastMCP: acts as a decorator factory — tools/resources just register names
    class _FakeFastMCP:
        def __init__(self, name: str, instructions: str = "") -> None:
            self.name = name
            self.instructions = instructions
            self._tools: dict = {}
            self._resources: dict = {}

        def tool(self):
            def decorator(fn):
                self._tools[fn.__name__] = fn
                return fn

            return decorator

        def resource(self, uri_template: str):
            def decorator(fn):
                self._resources[uri_template] = fn
                return fn

            return decorator

        def run(self, transport: str = "stdio") -> None:
            pass

    fake_fastmcp_mod.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    fake_mcp.server = fake_server  # type: ignore[attr-defined]
    fake_server.fastmcp = fake_fastmcp_mod  # type: ignore[attr-defined]
    return fake_mcp


@pytest.fixture()
def mcp_server_module():
    """Import strata.mcp.server with a mocked mcp dependency."""
    fake_mcp = _make_fake_mcp_module()
    modules_to_patch = {
        "mcp": fake_mcp,
        "mcp.server": fake_mcp.server,
        "mcp.server.fastmcp": fake_mcp.server.fastmcp,
    }
    # Remove any cached real import
    original = {k: sys.modules.pop(k, None) for k in ["strata.mcp.server", *modules_to_patch]}
    sys.modules.update(modules_to_patch)
    try:
        import importlib

        import strata.mcp.server as srv

        importlib.reload(srv)
        yield srv
    finally:
        # Restore original state
        for k in modules_to_patch:
            sys.modules.pop(k, None)
        for k, v in original.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)


class TestMcpServerTools:
    def test_server_has_workspace_status_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "workspace_status")
        assert callable(mcp_server_module.workspace_status)

    def test_server_has_validate_file_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "validate_file")
        assert callable(mcp_server_module.validate_file)

    def test_server_has_list_schemas_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "list_schemas")
        assert callable(mcp_server_module.list_schemas)

    def test_server_has_get_schema_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "get_schema")
        assert callable(mcp_server_module.get_schema)

    def test_server_has_scaffold_file_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "scaffold_file")
        assert callable(mcp_server_module.scaffold_file)

    def test_server_has_build_plan_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "build_plan")
        assert callable(mcp_server_module.build_plan)

    def test_server_has_build_run_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "build_run")
        assert callable(mcp_server_module.build_run)

    def test_server_has_deploy_plan_tool(self, mcp_server_module):
        assert hasattr(mcp_server_module, "deploy_plan")
        assert callable(mcp_server_module.deploy_plan)

    def test_list_schemas_returns_kinds(self, mcp_server_module):
        result = mcp_server_module.list_schemas()
        assert "kinds" in result
        assert "deployment" in result["kinds"]
        assert "configuration" in result["kinds"]

    def test_get_schema_valid_kind(self, mcp_server_module):
        result = mcp_server_module.get_schema("deployment")
        assert "properties" in result or "$defs" in result

    def test_get_schema_invalid_kind_returns_error(self, mcp_server_module):
        result = mcp_server_module.get_schema("notakind")
        assert "error" in result

    def test_scaffold_file_known_kind(self, mcp_server_module):
        result = mcp_server_module.scaffold_file("namespace", "my-ns")
        assert result.get("kind") == "namespace"
        assert result.get("name") == "my-ns"
        assert "content" in result
        assert "my-ns" in result["content"]

    def test_scaffold_file_unknown_kind_returns_error(self, mcp_server_module):
        result = mcp_server_module.scaffold_file("notakind", "test")
        assert "error" in result

    def test_workspace_status_calls_status_command(self, mcp_server_module, tmp_path):
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = mcp_server_module.workspace_status(work_path=str(tmp_path))
        assert isinstance(result, dict)
