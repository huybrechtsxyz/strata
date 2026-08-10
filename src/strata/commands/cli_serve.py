"""Click CLI wiring for the ``serve`` command group (ADR-0065 Phase 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.serve.health_serve_command import HealthServeCommand


@click.group(name="serve", help="Run and check the strata state-service server (ADR-0065).")
def serve_group() -> None:
    """Serve command group."""
    pass


@serve_group.command(name="run", help="Start the strata state-service server (foreground).")
@click.option(
    "--host",
    default="127.0.0.1",
    envvar="STRATA_SERVE_HOST",
    show_default=True,
    help="Bind address. [env: STRATA_SERVE_HOST]",
)
@click.option(
    "--port",
    default=8443,
    type=int,
    envvar="STRATA_SERVE_PORT",
    show_default=True,
    help="Bind port. [env: STRATA_SERVE_PORT]",
)
@click.option(
    "--tls-cert",
    "tls_cert",
    default=None,
    envvar="STRATA_SERVE_TLS_CERT",
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the TLS certificate file. [env: STRATA_SERVE_TLS_CERT]",
)
@click.option(
    "--tls-key",
    "tls_key",
    default=None,
    envvar="STRATA_SERVE_TLS_KEY",
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the TLS private key file. [env: STRATA_SERVE_TLS_KEY]",
)
def serve_run(host: str, port: int, tls_cert: Optional[str], tls_key: Optional[str]) -> None:
    """Launch the strata state-service server.

    Runs in the foreground — like `strata mcp serve`, lifecycle (start/stop/restart)
    is owned by whatever launches this process (systemd, a container runtime,
    Ctrl+C), not by a separate CLI command tracking a PID.

    Only `GET /healthz` exists at this step — no `/v1/events`, no database
    (ADR-0065 Step 2.1). Refuses to start on a non-loopback bind without TLS.

    Requires the optional server dependency:
        pip install xyz-strata[server]
    """
    from strata.server.config import ServerRuntimeConfig

    config = ServerRuntimeConfig(
        host=host,
        port=port,
        tls_cert=Path(tls_cert) if tls_cert else None,
        tls_key=Path(tls_key) if tls_key else None,
    )
    bind_error = config.validate_bind()
    if bind_error:
        raise click.ClickException(bind_error)

    try:
        import uvicorn

        from strata.server.app import create_app
    except ImportError as exc:
        raise click.ClickException(
            "The 'server' optional dependency is required.\nInstall it with: pip install xyz-strata[server]"
        ) from exc

    app = create_app()
    uvicorn.run(app, host=host, port=port, ssl_certfile=tls_cert, ssl_keyfile=tls_key)


@serve_group.command(name="health", help="Check reachability of a running state-service server.")
@click.argument("url")
@click.option("--timeout", default=5.0, type=float, show_default=True, help="Request timeout in seconds.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def serve_health(
    url: str,
    timeout: float = 5.0,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """GET <url>/healthz and report reachability."""
    command = HealthServeCommand(
        url=url, timeout=timeout, work_path=work_path, output=output, verbose=verbose, quiet=quiet
    )
    success = command.execute()
    handle_command_exit(command, success)
