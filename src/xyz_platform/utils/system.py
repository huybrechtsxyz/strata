#!/usr/bin/env python3
"""
===============================================================================
Script Name   : system.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Utility functions for system operations in xyz-platform.
===============================================================================
"""

import os
import re
import subprocess
import unicodedata
import uuid
import time

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union
from rich.console import Console
from xyz_platform.logger import get_logger

logger = get_logger(__name__)


def generate_uuid7() -> str:
    """
    Generate a time-ordered UUID.

    Uses ``uuid.uuid7()`` (Python 3.13+) when available for true UUID v7
    (RFC 9562) which is monotonically ordered by creation time.
    Falls back to ``uuid.uuid4()`` (random) on older Python versions.

    Returns:
        str: UUID formatted string
    """
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    return str(uuid.uuid4())


# Get the normalized path
def normalize_path(path: str) -> str:
    """Normalize a file path to an absolute path."""
    # Remove or replace invalid characters for Windows and Linux paths
    # Invalid chars: < > : " / \ | ? * and control characters (0-31)
    path = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", path)
    return path.strip(". ")


# Join multiple paths
def resolve_path(base_path: str, target_path: str = None, *sub_paths: str) -> Path:
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
                f"Absolute path not allowed in sub_paths: {sub_path}. "
                f"Use target_path parameter for absolute paths."
            )

    full_path = a_path.joinpath(*sub_paths)  # .resolve() ?
    return full_path


# Get the CLI version
def get_cli_version() -> str:
    """Get the version of the CLI tool."""
    from importlib.metadata import version, PackageNotFoundError
    import os
    import re

    # 1. Try importlib.metadata (installed package)
    try:
        pkg_version = version("xyz-platform")
        if pkg_version:
            return pkg_version
    except PackageNotFoundError:
        pass

    # 2. Try to read from pyproject.toml (source tree)
    try:
        root_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        pyproject_path = os.path.join(root_dir, "pyproject.toml")
        if os.path.exists(pyproject_path):
            with open(pyproject_path, "r", encoding="utf-8") as f:
                for line in f:
                    match = re.match(r"version\s*=\s*['\"]([^'\"]+)['\"]", line)
                    if match:
                        return match.group(1)
    except Exception:
        pass

    # 3. Fallback
    return "0.0.1-dev"


# Get the root path of the package
def get_root_path() -> Path:
    """Get the root path of the xyz-platform package."""
    package_root = Path(__file__).parent.parent
    return package_root


# Get temporary directory path
def get_temp_path(base_path: str = None, create: bool = True) -> Path:
    """
    Get the temporary directory path for the workspace.

    Args:
        base_path: Base workspace path. If None, uses system temp directory.
        create: Whether to create the directory if it doesn't exist.

    Returns:
        Path to temporary directory.
    """
    import tempfile

    if base_path:
        # Use workspace-specific temp directory
        temp_path = Path(base_path) / ".xyz-platform"
    else:
        # Use system temp directory
        temp_path = Path(tempfile.gettempdir()) / "xyz-platform"

    if create:
        temp_path.mkdir(parents=True, exist_ok=True)

    return temp_path


# Check if text starts with an emoji
def has_emoji_prefix(text: str) -> bool:
    if not text or len(text) == 0:
        return False
    first = text[0]
    # Check for common emoji/icon Unicode ranges
    return (
        "\U0001f300"
        <= first
        <= "\U0001faff"  # Misc symbols and pictographs, emoticons, transport, etc.
        or "\u2600" <= first <= "\u26ff"  # Misc symbols
        or "\u2700" <= first <= "\u27bf"  # Dingbats
        or unicodedata.category(first)
        in {"So", "Sk"}  # Symbol, other; Symbol, modifier
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
        extra={"command": cmd_display, "cwd": cwd, "timeout": timeout},
    )
    start_time = time.time()

    if show_output:
        Console.print(1, f"Running command: [yellow]{cmd_display}[/yellow]")

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
                Console.print(f"[dim]{result.stdout.strip()}[/dim]")
            if result.stderr:
                Console.print(f"[red]{result.stderr.strip()}[/red]")

        if check and result.returncode != 0:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "Command failed",
                extra={
                    "command": cmd_display,
                    "returncode": result.returncode,
                    "duration_ms": round(duration_ms, 2),
                    "stderr": result.stderr.strip() if result.stderr else "",
                },
            )
            raise subprocess.CalledProcessError(
                result.returncode, cmd_display, result.stdout, result.stderr
            )

        duration_ms = (time.time() - start_time) * 1000
        logger.debug(
            "Command completed",
            extra={
                "command": cmd_display,
                "returncode": result.returncode,
                "duration_ms": round(duration_ms, 2),
            },
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
            extra={"command": cmd_display, "error": str(e)},
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
