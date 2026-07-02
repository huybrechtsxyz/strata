"""Click CLI wiring for the ``mcp`` command group."""

import click


@click.group(name="mcp", help="Model Context Protocol server for AI tool integration.")
def mcp_group() -> None:
    """MCP command group."""
    pass


@mcp_group.command(name="serve", help="Start the strata MCP server (stdio transport).")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"]),
    default="stdio",
    show_default=True,
    help="Transport protocol. Use 'stdio' for VS Code / Claude Desktop.",
)
def mcp_serve(transport: str) -> None:
    """Launch the strata MCP server.

    VS Code and Claude Desktop connect via stdio (default).
    The server exposes workspace operations as MCP tools so AI assistants
    can query and act on the workspace without parsing CLI text output.

    The workspace root is resolved from the process working directory — set
    ``cwd`` to the workspace root in your MCP client configuration.

    Requires the optional mcp dependency:
        pip install xyz-strata[mcp]
    """
    try:
        from strata.mcp.server import mcp
    except ImportError as exc:
        raise click.ClickException(str(exc)) from exc

    mcp.run(transport=transport)
