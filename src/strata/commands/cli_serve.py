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
from strata.commands.serve.create_token_serve_command import CreateTokenServeCommand
from strata.commands.serve.health_serve_command import HealthServeCommand
from strata.commands.serve.list_tokens_serve_command import ListTokensServeCommand
from strata.commands.serve.migrate_serve_command import MigrateServeCommand
from strata.commands.serve.revoke_token_serve_command import RevokeTokenServeCommand
from strata.commands.serve.tail_serve_command import TailServeCommand

# Shared default/envvar for the event-store connection (ADR-0065 Step 2.2) — sqlite
# is the zero-config default; postgresql+psycopg://... / mssql+pyodbc://... are the
# opt-in production backends (server-postgres / server-mssql extras).
_DB_URL_OPTION = click.option(
    "--db-url",
    "db_url",
    default="sqlite:///.strata/state-service.db",
    envvar="STRATA_SERVE_DB_URL",
    show_default=True,
    help="Event-store connection URL (sqlite/postgresql/mssql). [env: STRATA_SERVE_DB_URL]",
)


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
@click.option(
    "--admin-token",
    "admin_token",
    default=None,
    envvar="STRATA_SERVE_ADMIN_TOKEN",
    help=(
        "Admin bearer token, enabling the /v1/tokens management routes (ADR-0065 Step 2.4). "
        "Omit to leave those routes unregistered entirely. [env: STRATA_SERVE_ADMIN_TOKEN]"
    ),
)
@click.option(
    "--oidc-issuer",
    "oidc_issuer",
    default=None,
    envvar="STRATA_SERVE_OIDC_ISSUER",
    help="OIDC issuer URL, enabling /auth/login + /auth/callback (ADR-0067 Step 7). [env: STRATA_SERVE_OIDC_ISSUER]",
)
@click.option(
    "--oidc-client-id",
    "oidc_client_id",
    default=None,
    envvar="STRATA_SERVE_OIDC_CLIENT_ID",
    help="OIDC client id. [env: STRATA_SERVE_OIDC_CLIENT_ID]",
)
@click.option(
    "--oidc-client-secret",
    "oidc_client_secret",
    default=None,
    envvar="STRATA_SERVE_OIDC_CLIENT_SECRET",
    help=("OIDC client secret. Omit for a public client using PKCE alone. [env: STRATA_SERVE_OIDC_CLIENT_SECRET]"),
)
@click.option(
    "--oidc-redirect-base",
    "oidc_redirect_base",
    default=None,
    envvar="STRATA_SERVE_OIDC_REDIRECT_BASE",
    help=(
        "Externally-visible base URL used to build the /auth/callback redirect URI "
        "(e.g. https://control-plane.example.com). [env: STRATA_SERVE_OIDC_REDIRECT_BASE]"
    ),
)
@click.option(
    "--session-secret",
    "session_secret",
    default=None,
    envvar="STRATA_SERVE_SESSION_SECRET",
    help=(
        "HMAC signing key for session tokens minted by /auth/callback. Required alongside "
        "the --oidc-* flags. [env: STRATA_SERVE_SESSION_SECRET]"
    ),
)
@_DB_URL_OPTION
def serve_run(
    host: str,
    port: int,
    tls_cert: Optional[str],
    tls_key: Optional[str],
    admin_token: Optional[str],
    oidc_issuer: Optional[str],
    oidc_client_id: Optional[str],
    oidc_client_secret: Optional[str],
    oidc_redirect_base: Optional[str],
    session_secret: Optional[str],
    db_url: str,
) -> None:
    """Launch the strata state-service server.

    Runs in the foreground — like `strata mcp serve`, lifecycle (start/stop/restart)
    is owned by whatever launches this process (systemd, a container runtime,
    Ctrl+C), not by a separate CLI command tracking a PID.

    `GET /healthz` also verifies database connectivity (ADR-0065 Step 2.2).
    `POST /v1/events` requires a per-workspace bearer token (Step 2.4) — issue one
    via `strata serve token create` once `--admin-token` is configured here.
    Refuses to start on a non-loopback bind without TLS. Run
    `strata serve migrate --db-url ...` first to create the schema — this command
    only ever issues INSERT/SELECT, never CREATE TABLE.

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

    oidc_fields = {"issuer": oidc_issuer, "client_id": oidc_client_id, "redirect_base": oidc_redirect_base}
    configured_oidc_fields = {k: v for k, v in oidc_fields.items() if v}
    if configured_oidc_fields and len(configured_oidc_fields) != len(oidc_fields):
        missing = ", ".join(f"--oidc-{k.replace('_', '-')}" for k, v in oidc_fields.items() if not v)
        raise click.ClickException(
            f"OIDC login requires all of --oidc-issuer/--oidc-client-id/--oidc-redirect-base. Missing: {missing}"
        )
    if configured_oidc_fields and not session_secret:
        raise click.ClickException("OIDC login also requires --session-secret to sign session tokens.")

    try:
        import uvicorn

        from strata.server.app import create_app
        from strata.server.db.engine import create_engine_from_url
    except ImportError as exc:
        raise click.ClickException(
            "The 'server' optional dependency is required.\nInstall it with: pip install xyz-strata[server]"
        ) from exc

    try:
        engine = create_engine_from_url(db_url)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    oidc_config = None
    if configured_oidc_fields:
        from strata.server.auth.oidc_relying_party import OidcRelyingPartyConfig

        oidc_config = OidcRelyingPartyConfig(
            issuer=oidc_issuer,  # type: ignore[arg-type]
            client_id=oidc_client_id,  # type: ignore[arg-type]
            redirect_base=oidc_redirect_base,  # type: ignore[arg-type]
            client_secret=oidc_client_secret,
        )

    app = create_app(engine, admin_token=admin_token, oidc_config=oidc_config, session_secret=session_secret)
    uvicorn.run(app, host=host, port=port, ssl_certfile=tls_cert, ssl_keyfile=tls_key)


@serve_group.command(name="migrate", help="Apply/verify the event-store schema (run separately from `serve run`).")
@_DB_URL_OPTION
@click_output_format
@click_output_verbose
@click_output_quiet
def serve_migrate(
    db_url: str,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Create or verify the `events` table against the configured database.

    Run separately from `serve run` — a deliberate privilege split: this is the
    one place anything needs CREATE TABLE/ALTER TABLE rights.
    """
    command = MigrateServeCommand(db_url=db_url, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


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


@serve_group.command(name="tail", help="Show the most recent events on a running server (ADR-0065 Step 2.6).")
@click.argument("url")
@click.option(
    "--token",
    "token",
    required=True,
    envvar="STRATA_SERVE_TOKEN",
    help="Ingest or admin bearer token. [env: STRATA_SERVE_TOKEN]",
)
@click.option("--limit", "limit", default=100, type=int, show_default=True, help="Max rows to return (server-capped).")
@click.option(
    "--workspace",
    "workspace",
    default=None,
    help="Filter by workspace. Ignored/overridden server-side if the token is a per-workspace ingest token.",
)
@click.option("--timeout", default=10.0, type=float, show_default=True, help="Request timeout in seconds.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def serve_tail(
    url: str,
    token: str,
    limit: int = 100,
    workspace: Optional[str] = None,
    timeout: float = 10.0,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """GET <url>/v1/events/tail and print the most recent events (lean projection, no full payload)."""
    command = TailServeCommand(
        url=url,
        token=token,
        limit=limit,
        workspace=workspace,
        timeout=timeout,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# --url / --admin-token — shared by every `serve token` subcommand (ADR-0065 Step
# 2.4). These are HTTP clients against a running server's /v1/tokens routes, not
# direct-DB commands like `migrate` — issuing an ingest token is a routine,
# ongoing operation, unlike schema setup, so it goes through the same interface
# every other client uses rather than requiring a separate DB credential.
_SERVER_URL_OPTION = click.option(
    "--url",
    "url",
    required=True,
    help="Base URL of the running state-service server.",
)
_ADMIN_TOKEN_OPTION = click.option(
    "--admin-token",
    "admin_token",
    required=True,
    envvar="STRATA_SERVE_ADMIN_TOKEN",
    help="Admin bearer token for the running server. [env: STRATA_SERVE_ADMIN_TOKEN]",
)


@serve_group.group(name="token", help="Manage per-workspace ingest tokens on a running server.")
def serve_token_group() -> None:
    """Token management subgroup."""
    pass


@serve_token_group.command(name="create", help="Create a new per-workspace ingest token.")
@_SERVER_URL_OPTION
@_ADMIN_TOKEN_OPTION
@click.option("--workspace", "workspace", required=True, help="Workspace name this token is issued to.")
@click.option("--timeout", default=10.0, type=float, show_default=True, help="Request timeout in seconds.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def serve_token_create(
    url: str,
    admin_token: str,
    workspace: str,
    timeout: float = 10.0,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Create a new ingest token. The secret is shown exactly once — save it now."""
    command = CreateTokenServeCommand(
        url=url,
        admin_token=admin_token,
        workspace=workspace,
        timeout=timeout,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@serve_token_group.command(name="list", help="List ingest tokens (never the secret).")
@_SERVER_URL_OPTION
@_ADMIN_TOKEN_OPTION
@click.option("--workspace", "workspace", default=None, help="Filter by workspace.")
@click.option("--timeout", default=10.0, type=float, show_default=True, help="Request timeout in seconds.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def serve_token_list(
    url: str,
    admin_token: str,
    workspace: Optional[str] = None,
    timeout: float = 10.0,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List tokens — token_id/workspace/created_at/revoked_at only."""
    command = ListTokensServeCommand(
        url=url,
        admin_token=admin_token,
        workspace=workspace,
        timeout=timeout,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@serve_token_group.command(name="revoke", help="Revoke an ingest token by id.")
@click.argument("token_id")
@_SERVER_URL_OPTION
@_ADMIN_TOKEN_OPTION
@click.option("--timeout", default=10.0, type=float, show_default=True, help="Request timeout in seconds.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def serve_token_revoke(
    token_id: str,
    url: str,
    admin_token: str,
    timeout: float = 10.0,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Revoke a token — rejected on its very next use."""
    command = RevokeTokenServeCommand(
        url=url,
        admin_token=admin_token,
        token_id=token_id,
        timeout=timeout,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
