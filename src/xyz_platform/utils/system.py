#!/usr/bin/env python3
"""Utility functions for system operations."""

import os
import re
import subprocess
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from rich.console import Console

from xyz_platform.logger import get_logger

logger = get_logger(__name__)


def generate_uuid() -> str:
    """
    Generate a time-ordered UUID.

    Uses ``uuid.uuid7()`` (Python 3.13+) when available for true UUID v7
    (RFC 9562) which is monotonically ordered by creation time.
    Falls back to ``uuid.uuid4()`` (random) on older Python versions.

    Returns:
        str: UUID formatted string
    """
    # if hasattr(uuid, "uuid7"):
    #    return str(uuid.uuid7())
    return str(uuid.uuid4())


# Get the normalized path
def normalize_path(path: str) -> str:
    """Normalize a file path to an absolute path."""
    # Remove or replace invalid characters for Windows and Linux paths
    # Invalid chars: < > : " / \ | ? * and control characters (0-31)
    path = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", path)
    return path.strip(". ")


# Join multiple paths
def resolve_path(
    base_path: str,
    target_path: Optional[str] = None,
    *sub_paths: str,
    repo_map: Optional[Dict[str, str]] = None,
) -> Path:
    """
    Resolve the target path by joining base path, target path, and sub-paths.
    arguments:
        base_path: The base directory path or current working directory if None.
        target_path: if the target is a directory path. If provided, it is used as the base.
        sub_paths: Additional sub-paths to join to the base/target path.
    returns:
        The resolved full path as a Path object
    """
    if base_path is None or base_path == "":
        base_path = os.getcwd()

    # Normalise to str — callers may pass Path objects
    base_path = str(base_path)
    if target_path is not None:
        target_path = str(target_path)

    # Resolve @repo_name/... cross-repo references
    if target_path and target_path.startswith("@"):
        repo_name, _, rest = target_path[1:].partition("/")
        if repo_map is None or repo_name not in repo_map:
            raise ValueError(f"Unknown repo reference '@{repo_name}' — no repo_map provided or repo not found")
        target_path = repo_map[repo_name] + ("/" + rest if rest else "")

    # If target_path is absolute, use it directly
    if target_path is not None and target_path != "":
        target_path_obj = Path(target_path)
        if target_path_obj.is_absolute():
            a_path = target_path_obj
        else:
            a_path = Path(base_path) / target_path_obj
    else:
        a_path = Path(base_path)

    # If no sub_paths, return the base/target path
    if sub_paths is None or len(sub_paths) == 0:
        return a_path

    # If subpaths are provided, join them
    # What happens if sub_paths contain absolute paths? Validate sub_paths are relative
    # Path.joinpath ignores previous parts if an absolute path is encountered
    for sub_path in sub_paths:
        sub_path_obj = Path(sub_path)
        if sub_path_obj.is_absolute():
            raise ValueError(
                f"Absolute path not allowed in sub_paths: {sub_path}. Use target_path parameter for absolute paths."
            )

    full_path = a_path.joinpath(*sub_paths)  # .resolve() ?
    return full_path


def resolve_work_path(explicit: Optional[str] = None) -> Path:
    """
    Resolve the workspace root using a three-level priority chain.

    1. *explicit* — a path supplied directly by the caller (``--work-path`` or
       ``XYZ_WORK_PATH`` env var).  Returned as-is (resolved to absolute).
    2. Upward walk — starting from ``Path.cwd()``, walk up through parent
       directories until a ``.platform/`` directory is found.  The directory
       that contains ``.platform/`` is returned as the workspace root.
    3. CWD — fallback when no ``.platform/`` ancestor can be found.

    Args:
        explicit: Optional explicit path string.  Pass ``None`` or ``""`` to
                  trigger automatic discovery.

    Returns:
        Resolved :class:`pathlib.Path` for the workspace root.
    """
    if explicit:
        return Path(explicit).resolve()

    # Walk up from CWD looking for .platform/
    candidate = Path.cwd().resolve()
    while True:
        if (candidate / ".platform").is_dir():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    return Path.cwd().resolve()


# Get the root path of the package
def get_pkg_root_path() -> Path:
    """Get the root path of the xyz-platform package."""
    package_root = Path(__file__).parent.parent
    return package_root


# Get the path to the templates directory
def get_pkg_templates_path() -> Path:
    """Get the path to the package templates directory."""
    return get_pkg_root_path() / "templates"


# Get the path to the data directory
def get_pkg_data_path() -> Path:
    """Get the path to the data directory within the package."""
    return get_pkg_root_path() / "data"


# Get the path to the default configuration file
def get_pgk_config_path(data_path: Optional[Path]) -> Path:
    """Get the path to the default configuration file."""
    if data_path is not None:
        return data_path / "configuration.yaml"
    return get_pkg_data_path() / "configuration.yaml"


# Get the path to the default logging configuration file
def get_pkg_logging_path(data_path: Optional[Path] = None) -> Path:
    """Get the path to the default logging configuration file."""
    if data_path is not None:
        return data_path / "logging.yaml"
    return get_pkg_data_path() / "logging.yaml"


# Check if text starts with an emoji
def has_emoji_prefix(text: str) -> bool:
    if not text or len(text) == 0:
        return False
    first = text[0]
    # Check for common emoji/icon Unicode ranges
    return (
        "\U0001f300" <= first <= "\U0001faff"  # Misc symbols and pictographs, emoticons, transport, etc.
        or "\u2600" <= first <= "\u26ff"  # Misc symbols
        or "\u2700" <= first <= "\u27bf"  # Dingbats
        or unicodedata.category(first) in {"So", "Sk"}  # Symbol, other; Symbol, modifier
    )


@dataclass
class CommandResult:
    """Result of a command execution."""

    returncode: int
    stdout: str
    stderr: str
    command: str
    duration_ms: float

    @property
    def is_successful(self) -> bool:
        """Check if command succeeded (returncode == 0)."""
        return self.returncode == 0

    @property
    def has_output(self) -> bool:
        """Check if command produced stdout output."""
        return bool(self.stdout)

    @property
    def has_errors(self) -> bool:
        """Check if command produced stderr output."""
        return bool(self.stderr)

    def __str__(self) -> str:
        """String representation of command result."""
        status = "SUCCESS" if self.is_successful else f"FAILED (exit {self.returncode})"
        return f"CommandResult[{status}]: {self.command} ({self.duration_ms:.2f}ms)"


# Run a shell command
def run_command(
    command: Union[str, List[str]],
    show_output: bool = False,
    check: bool = False,
    timeout: Optional[int] = None,
    capture_output: Optional[bool] = None,
    cwd: Optional[str] = None,
) -> CommandResult:
    """
    Run a shell command and capture its output.

    Args:
        command: The command to run (string or List of args).
        show_output: If True, prints the command and live output.
        check: If True, raise an exception if returncode != 0.
        timeout: Timeout in seconds for command execution.
        capture_output: If True, capture stdout/stderr. If False, allow interactive. If None, auto-detect based on show_output.
        cwd: Working directory for command execution.

    Returns:
        CommandResult object with returncode, stdout, stderr, command, and duration_ms.
        Use result.is_successful to check if command succeeded.

    Example:
        result = run_command("ls -la")
        if result.is_successful:
            print(result.stdout)
    """
    if isinstance(command, List):
        cmd_display = " ".join(command)
    else:
        cmd_display = command

    logger.debug(
        "Executing command",
        command=cmd_display,
        cwd=cwd,
        timeout=timeout,
    )
    start_time = time.time()
    console = Console()

    if show_output:
        console.print(f"Running command: [yellow]{cmd_display}[/yellow]")

    # Auto-detect capture_output if not specified
    if capture_output is None:
        capture_output = True  # Default to capturing output

    try:
        result = subprocess.run(
            command,
            shell=isinstance(command, str),
            check=False,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            env=os.environ.copy(),  # Explicitly pass environment variables
            timeout=timeout,
            cwd=cwd,
        )

        if show_output and capture_output:
            if result.stdout:
                console.print(f"[dim]{result.stdout.strip()}[/dim]")
            if result.stderr:
                console.print(f"[red]{result.stderr.strip()}[/red]")

        if check and result.returncode != 0:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "Command failed",
                command=cmd_display,
                returncode=result.returncode,
                duration_ms=round(duration_ms, 2),
                stderr=result.stderr.strip() if result.stderr else "",
            )
            raise subprocess.CalledProcessError(result.returncode, cmd_display, result.stdout, result.stderr)

        duration_ms = (time.time() - start_time) * 1000
        logger.debug(
            "Command completed",
            command=cmd_display,
            returncode=result.returncode,
            duration_ms=round(duration_ms, 2),
        )

        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout.strip() if result.stdout else "",
            stderr=result.stderr.strip() if result.stderr else "",
            command=cmd_display,
            duration_ms=round(duration_ms, 2),
        )

    except subprocess.CalledProcessError:
        # Re-raise CalledProcessError when check=True
        raise
    except FileNotFoundError as e:
        # Handle command not found in PATH
        duration_ms = (time.time() - start_time) * 1000
        logger.debug(
            "Command not found in PATH",
            command=cmd_display,
            error=str(e),
        )
        return CommandResult(
            returncode=127,  # Standard "command not found" exit code
            stdout="",
            stderr=f"Command not found: {cmd_display}",
            command=cmd_display,
            duration_ms=round(duration_ms, 2),
        )
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.exception("Command execution failed with exception")
        return CommandResult(
            returncode=-1,
            stdout="",
            stderr=str(e),
            command=cmd_display,
            duration_ms=round(duration_ms, 2),
        )
