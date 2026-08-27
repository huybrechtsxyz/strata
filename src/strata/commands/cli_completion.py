"""Click CLI wiring for the ``strata completion`` command.

Generates shell completion scripts using Click's built-in machinery so users
can enable tab-completion for the ``strata`` CLI in their shell of choice.

Supported shells: bash, zsh, fish, powershell

Usage examples
--------------
bash / zsh — evaluate on every shell start::

    # bash
    echo 'eval "$(_STRATA_COMPLETE=bash_source strata)"' >> ~/.bashrc

    # zsh
    echo 'eval "$(_STRATA_COMPLETE=zsh_source strata)"' >> ~/.zshrc

    # Or redirect the script to a file and source it:
    strata completion bash > ~/.strata-completion.bash
    echo "source ~/.strata-completion.bash" >> ~/.bashrc

fish::

    strata completion fish > ~/.config/fish/completions/strata.fish

PowerShell — add to $PROFILE::

    strata completion powershell | Out-String | Invoke-Expression
"""

import click

# ---------------------------------------------------------------------------
# Supported shell names exposed as a Click Choice
# ---------------------------------------------------------------------------

_SHELLS = ["bash", "zsh", "fish", "powershell"]

# ---------------------------------------------------------------------------
# Install-hint comments prepended to each generated script
# ---------------------------------------------------------------------------

_INSTALL_HINTS: dict[str, str] = {
    "bash": (
        "# strata shell completion for bash\n"
        "# Add to ~/.bashrc or ~/.bash_profile:\n"
        '#   eval "$(_STRATA_COMPLETE=bash_source strata)"\n'
        "# Or save this output and source the file:\n"
        "#   strata completion bash > ~/.strata-completion.bash\n"
        "#   source ~/.strata-completion.bash\n"
    ),
    "zsh": (
        "# strata shell completion for zsh\n"
        "# Add to ~/.zshrc:\n"
        '#   eval "$(_STRATA_COMPLETE=zsh_source strata)"\n'
        "# Or save this output and source the file:\n"
        "#   strata completion zsh > ~/.strata-completion.zsh\n"
        "#   source ~/.strata-completion.zsh\n"
    ),
    "fish": (
        "# strata shell completion for fish\n"
        "# Save to the fish completions directory:\n"
        "#   strata completion fish > ~/.config/fish/completions/strata.fish\n"
    ),
    "powershell": (
        "# strata shell completion for PowerShell\n"
        "# Add to your $PROFILE:\n"
        "#   strata completion powershell | Out-String | Invoke-Expression\n"
    ),
}


def _completion_source(shell: str) -> str:
    """Return the completion script for *shell* by driving Click's own machinery.

    The import of ``strata.cli.main`` is deferred to avoid a circular import at
    module load time — ``cli.py`` imports this module, so importing ``strata.cli``
    here must only happen at *call* time (after ``cli.py`` is fully loaded).
    """
    from click.shell_completion import BashComplete, FishComplete, ShellComplete, ZshComplete

    from strata.cli import main as _main  # deferred — see docstring

    _classes = {
        "bash": BashComplete,
        "zsh": ZshComplete,
        "fish": FishComplete,
    }

    # Explicit annotation: without it, mypy infers cls's type from whichever
    # assignment it sees first (here, the "powershell" branch's
    # type[PowerShellComplete]), then rejects the other branch's
    # type[ShellComplete] assignment as incompatible.
    cls: type[ShellComplete]
    if shell == "powershell":
        try:
            from click.shell_completion import PowerShellComplete  # type: ignore[attr-defined]  # Click ≥ 8.1

            cls = PowerShellComplete
        except ImportError as err:
            raise click.ClickException(
                "PowerShell completion requires Click >= 8.1. Upgrade with: uv add 'click>=8.1'"
            ) from err
    else:
        cls = _classes[shell]

    complete = cls(
        cli=_main,
        ctx_args={},
        prog_name="strata",
        complete_var="_STRATA_COMPLETE",
    )
    hint = _INSTALL_HINTS.get(shell, "")
    return hint + complete.source()


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@click.command("completion")
@click.argument("shell", type=click.Choice(_SHELLS, case_sensitive=False))
def completion_command(shell: str) -> None:
    """Output shell completion script for SHELL.

    \b
    Supported shells:
      bash, zsh, fish, powershell

    \b
    Quick setup:
      bash:        strata completion bash >> ~/.bashrc
      zsh:         strata completion zsh  >> ~/.zshrc
      fish:        strata completion fish > ~/.config/fish/completions/strata.fish
      powershell:  strata completion powershell | Out-String | Invoke-Expression
    """
    try:
        source = _completion_source(shell.lower())
    except click.ClickException:
        raise
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(f"Could not generate completion script: {exc}") from exc

    click.echo(source, nl=False)
