"""Click CLI wiring for the help command."""

from pathlib import Path
from typing import Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.commands.cli_common import click_work_path
from xyz_platform.utils.config import DOCS_URL, SOLUTION_DIR
from xyz_platform.utils.system import resolve_work_path

# ---------------------------------------------------------------------------
# Topic registry — name → (description, filename)
# ---------------------------------------------------------------------------

_TOPICS: dict[str, tuple[str, str]] = {
    # Platform guides
    "quickstart": ("Get from a fresh install to a running workspace", "quickstart.md"),
    "workspace": ("What .platform/ is and how it is structured", "workspace.md"),
    "profiles": ("Model dev/stg/prd environments with profiles", "profiles.md"),
    "refs": ("Env files, config files, and how they layer", "refs.md"),
    "config-merge": ("How xyz deep-merges multiple config files", "config-merge.md"),
    "cross-repo": ("Using @repo_name/ references across repos", "cross-repo.md"),
    "environments": ("Mapping profiles to deployment environments", "environments.md"),
    "troubleshooting": ("Common errors and exactly how to fix them", "troubleshooting.md"),
    # Integrations
    "git": ("Git: repository clone, fetch, push, branch, and diff operations", "git.md"),
    "terraform": ("Terraform: IaC provisioner used by the build and deploy pipeline", "terraform.md"),
    "docker": ("Docker: container runtime used by the platform", "docker.md"),
    "azure_appconfig": ("Azure App Configuration: externalized key-value config for profiles", "azure_appconfig.md"),
    "azure_keyvault": ("Azure Key Vault: secret resolution for refs and deploy values", "azure_keyvault.md"),
    "bitwarden": ("Bitwarden Secrets Manager: secret resolution for refs and deploy values", "bitwarden.md"),
    "hashicorp_consul": ("HashiCorp Consul: service discovery and distributed config", "hashicorp_consul.md"),
    "hashicorp_vault": ("HashiCorp Vault: secret management and dynamic credentials", "hashicorp_vault.md"),
}

_HELP_DATA_DIR = Path(__file__).parent.parent / "data" / "help"

_WORKSPACE_HELP_DIR = "help"  # relative to .platform/


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workspace_help_dir(work_path: Optional[Path]) -> Optional[Path]:
    """Return <work_path>/.platform/help/ if it exists, else None."""
    if work_path is None:
        return None
    d = work_path / SOLUTION_DIR / _WORKSPACE_HELP_DIR
    return d if d.is_dir() else None


def _first_line(md_file: Path) -> str:
    """Return the first non-empty line of a markdown file, stripped of leading # and spaces."""
    try:
        return next(
            (ln.lstrip("# ").strip() for ln in md_file.read_text(encoding="utf-8").splitlines() if ln.strip()),
            md_file.stem,
        )[:60]
    except OSError:
        return md_file.stem


def _topic_file(name: str, work_path: Optional[Path]) -> Optional[Path]:
    """Resolve the file for *name* using priority order:

    1. ``.platform/help/<name>.md``  — workspace custom / override
    2. ``data/help/<name>.md``       — package built-ins (guides + integrations)
    """
    ws_dir = _workspace_help_dir(work_path)
    if ws_dir is not None:
        f = ws_dir / f"{name}.md"
        if f.exists():
            return f

    entry = _TOPICS.get(name)
    if entry is not None:
        f = _HELP_DATA_DIR / entry[1]
        if f.exists():
            return f

    return None


def _all_topics(work_path: Optional[Path]) -> dict[str, tuple[str, str]]:
    """Return ordered {name: (description, section)} for --list.

    Sections: ``"built-in"``, ``"workspace"``.
    Workspace entries override built-ins of the same name.
    """
    # Built-ins first (platform guides + integrations)
    topics: dict[str, tuple[str, str]] = {name: (desc, "built-in") for name, (desc, _) in _TOPICS.items()}

    # Workspace custom — may override built-in topics
    ws_dir = _workspace_help_dir(work_path)
    if ws_dir is not None:
        for md_file in sorted(ws_dir.glob("*.md")):
            name = md_file.stem
            topics[name] = (_first_line(md_file), "workspace")

    return topics


def _render_topic_list(work_path: Optional[Path]) -> None:
    """Print topics grouped by section."""
    topics = _all_topics(work_path)

    sections: dict[str, list[tuple[str, str]]] = {
        "built-in": [],
        "workspace": [],
    }
    for name, (desc, section) in topics.items():
        sections[section].append((name, desc))

    col = max(len(n) for n in topics) + 2 if topics else 16

    section_labels = {
        "built-in": "Built-in topics",
        "workspace": f"Workspace topics  ({SOLUTION_DIR}/help/)",
    }
    first = True
    for key, label in section_labels.items():
        entries = sections[key]
        if not entries:
            continue
        if not first:
            click.echo("")
        click.echo(f"  {label}:\n")
        for name, desc in entries:
            click.echo(f"    {name:<{col}}{desc}")
        first = False

    click.echo("\n  Use: xyz help --topic <name>")


def _render_topic(name: str, work_path: Optional[Path]) -> bool:
    """Print the content of a topic file.  Returns False if not found."""
    topic_path = _topic_file(name, work_path)
    if topic_path is None:
        click.echo(f"  Unknown topic: '{name}'.\n", err=True)
        _render_topic_list(work_path)
        return False

    content = topic_path.read_text(encoding="utf-8")
    click.echo("")
    for line in content.splitlines():
        click.echo(f"  {line}" if line else "")
    click.echo("")
    return True


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command("help")
@click_work_path
@click.option(
    "--list",
    "list_topics",
    is_flag=True,
    default=False,
    help="List all available help topics.",
)
@click.option(
    "--topic",
    default=None,
    metavar="NAME",
    help="Display help for a specific topic.",
)
def help_command(
    work_path: Optional[str] = None,
    list_topics: bool = False,
    topic: Optional[str] = None,
) -> None:
    """Show help topics and workflow guidance.

    Run without arguments for a getting-started overview.
    Use --list to see all available topics.
    Use --topic <name> to read a specific topic.

    Per-workspace custom topics can be placed in .platform/help/<name>.md.
    They appear in --list and can override built-in topics of the same name.
    """
    resolved_work_path: Optional[Path] = None
    try:
        resolved_work_path = resolve_work_path(work_path)
    except Exception:
        pass  # no workspace — fine, help still works without one

    # --topic takes precedence over --list
    if topic is not None:
        BaseCommand.show_console_header(work_path=None)
        ok = _render_topic(topic, resolved_work_path)
        BaseCommand.show_console_footer()
        if not ok:
            raise click.exceptions.Exit(1)
        return

    if list_topics:
        BaseCommand.show_console_header(work_path=None)
        click.echo("")
        _render_topic_list(resolved_work_path)
        click.echo("")
        BaseCommand.show_console_footer()
        return

    # Default: curated getting-started overview
    BaseCommand.show_console_header(work_path=None)
    click.echo("")
    click.echo("  XYZ Platform CLI automates multi-repo workspace preparation,")
    click.echo("  configuration merging, and deployment execution.")
    click.echo("")
    click.echo("  Canonical workflow:")
    click.echo("")
    click.echo("    xyz init --name myproject                   # create workspace")
    click.echo("    xyz repo add myrepo <url>                   # register a repo")
    click.echo("    xyz profile add dev                         # create a profile")
    click.echo("    xyz profile activate dev                    # set active profile")
    click.echo("    xyz ref envfile add --profile dev \\")
    click.echo("        --name base --path ./dev.env            # register env file")
    click.echo("    xyz ref configfile add --profile dev \\")
    click.echo("        --name app --path ./app.yaml            # register config file")
    click.echo("    xyz build                                   # merge + build artifacts")
    click.echo("    xyz deploy                                  # execute deployment")
    click.echo("")
    click.echo("  Command groups:")
    click.echo("")
    groups = [
        ("init", "Initialize a new solution workspace"),
        ("clean", "Remove workspace state and artifacts"),
        ("repo", "Register and sync git repositories"),
        ("profile", "Manage dev/stg/prd profiles"),
        ("ref", "Register env, config, data, and secret file refs"),
        ("config", "Manage persistent CLI defaults"),
        ("log", "View execution logs and manage logging config"),
        ("version", "Show CLI version"),
    ]
    for name, desc in groups:
        click.echo(f"    {name:<10}{desc}")
    click.echo("")
    click.echo("  For per-command usage:  xyz <command> --help")
    click.echo("  For topic guides:       xyz help --list")
    click.echo(f"  Full documentation:     {DOCS_URL}")
    click.echo("")
    BaseCommand.show_console_footer()
