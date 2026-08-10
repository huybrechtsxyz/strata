"""Tests for the ``serve`` command group (ADR-0065 Phase 2, Steps 2.1-2.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_serve import serve_group

_real_import = __import__


def _import_side_effect(name: str, *args: Any, **kwargs: Any) -> Any:
    if name == "uvicorn":
        raise ImportError("uvicorn not installed")
    return _real_import(name, *args, **kwargs)


class TestServeCliGroup:
    def test_group_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve_group, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "health" in result.output
        assert "migrate" in result.output

    def test_run_subcommand_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve_group, ["run", "--help"])
        assert result.exit_code == 0

    def test_run_shows_host_port_tls_options(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve_group, ["run", "--help"])
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--tls-cert" in result.output
        assert "--tls-key" in result.output

    def test_health_subcommand_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve_group, ["health", "--help"])
        assert result.exit_code == 0


class TestServeRunBindSafety:
    def test_non_loopback_without_tls_refused_before_import(self) -> None:
        """Bind validation happens before uvicorn/fastapi are ever imported."""
        runner = CliRunner()
        with patch("builtins.__import__", side_effect=_import_side_effect):
            # uvicorn is never even attempted — the bind check must fail first.
            result = runner.invoke(serve_group, ["run", "--host", "0.0.0.0"])
        assert result.exit_code != 0
        assert "TLS" in str(result.output) or "TLS" in str(result.exception)

    def test_loopback_without_tls_passes_bind_check(self) -> None:
        """Loopback bind reaches the (faked-missing) uvicorn import and fails there instead."""
        runner = CliRunner()
        with patch("builtins.__import__", side_effect=_import_side_effect):
            result = runner.invoke(serve_group, ["run", "--host", "127.0.0.1"])
        assert result.exit_code != 0
        assert "server" in result.output.lower()
        assert "pip install" in result.output


class TestServeRunFailsGracefullyWithoutServerExtra:
    def test_run_raises_click_exception_when_uvicorn_missing(self) -> None:
        runner = CliRunner()
        with patch("builtins.__import__", side_effect=_import_side_effect):
            result = runner.invoke(serve_group, ["run"])
        assert result.exit_code != 0
        assert "xyz-strata[server]" in result.output


class TestServeRunWiresUvicornCorrectly:
    def test_run_calls_uvicorn_with_expected_kwargs(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_text("cert")
        key.write_text("key")

        fake_app = object()
        mock_uvicorn = MagicMock()

        with (
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
            patch("strata.server.app.create_app", return_value=fake_app),
        ):
            runner = CliRunner()
            result = runner.invoke(
                serve_group,
                [
                    "run",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9443",
                    "--tls-cert",
                    str(cert),
                    "--tls-key",
                    str(key),
                ],
            )

        assert result.exit_code == 0, result.output
        mock_uvicorn.run.assert_called_once_with(
            fake_app, host="0.0.0.0", port=9443, ssl_certfile=str(cert), ssl_keyfile=str(key)
        )


class TestServeHealth:
    def test_health_reachable_exits_zero(self) -> None:
        runner = CliRunner()
        mock_response = MagicMock(status_code=200)
        with patch("requests.get", return_value=mock_response):
            result = runner.invoke(serve_group, ["health", "https://example.test"])
        assert result.exit_code == 0

    def test_health_non_200_exits_nonzero(self) -> None:
        runner = CliRunner()
        mock_response = MagicMock(status_code=503)
        with patch("requests.get", return_value=mock_response):
            result = runner.invoke(serve_group, ["health", "https://example.test"])
        assert result.exit_code != 0

    def test_health_connection_error_exits_nonzero(self) -> None:
        import requests

        runner = CliRunner()
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            result = runner.invoke(serve_group, ["health", "https://example.test"])
        assert result.exit_code != 0

    def test_health_json_output_reports_reachable(self) -> None:
        runner = CliRunner()
        mock_response = MagicMock(status_code=200)
        with patch("requests.get", return_value=mock_response):
            result = runner.invoke(serve_group, ["health", "https://example.test", "--output", "json"])
        assert result.exit_code == 0
        assert '"reachable": true' in result.output


class TestServeMigrate:
    def test_migrate_help_shows_db_url_option(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve_group, ["migrate", "--help"])
        assert result.exit_code == 0
        assert "--db-url" in result.output

    def test_migrate_creates_schema_against_sqlite_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.db"
        runner = CliRunner()
        result = runner.invoke(serve_group, ["migrate", "--db-url", f"sqlite:///{db_path}"])
        assert result.exit_code == 0, result.output
        assert db_path.exists()

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.db"
        runner = CliRunner()
        first = runner.invoke(serve_group, ["migrate", "--db-url", f"sqlite:///{db_path}"])
        second = runner.invoke(serve_group, ["migrate", "--db-url", f"sqlite:///{db_path}"])
        assert first.exit_code == 0
        assert second.exit_code == 0

    def test_migrate_unsupported_backend_exits_nonzero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve_group, ["migrate", "--db-url", "mysql://user:pass@localhost/db"])
        assert result.exit_code != 0

    def test_migrate_fails_gracefully_when_server_extra_missing(self) -> None:
        runner = CliRunner()
        with patch("builtins.__import__", side_effect=_import_side_effect_for_sqlalchemy):
            result = runner.invoke(serve_group, ["migrate", "--db-url", "sqlite:///:memory:"])
        assert result.exit_code != 0
        assert "xyz-strata[server]" in result.output


def _import_side_effect_for_sqlalchemy(name: str, *args: Any, **kwargs: Any) -> Any:
    if name == "strata.server.db.schema":
        raise ImportError("sqlalchemy not installed")
    return _real_import(name, *args, **kwargs)
