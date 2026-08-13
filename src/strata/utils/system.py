#!/usr/bin/env python3
"""Utility functions for system operations."""

import os
import re
import subprocess
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Dict, List, Optional, Union

from rich.console import Console

from strata.logger import get_logger

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
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    return str(uuid.uuid4())


# Flag names whose value (the next argv token, or the part after "=") is
# sensitive and must never be persisted or echoed in cleartext.
_SENSITIVE_ARGV_FLAGS = frozenset(
    {
        "--value",
        "--password",
        "--secret",
        "--token",
        "--api-key",
        "--apikey",
        "--credential",
        "--client-secret",
        "--private-key",
    }
)

_ARGV_REDACTION_MASK = "***REDACTED***"


def redact_argv(argv: List[str], mask: str = _ARGV_REDACTION_MASK) -> List[str]:
    """Return a copy of *argv* with values of known-sensitive flags masked.

    Some Strata commands receive secret material as CLI arguments (e.g.
    ``secret put KEY --value <plaintext>``). Several call sites echo or
    persist the raw process argv verbatim — the console header, the audit
    log, and the unhandled-error banner — which would otherwise write
    plaintext secrets to disk (``.strata/deploy-log/*.json``, log files) or
    the terminal. This helper masks the value that follows a sensitive flag,
    whether passed as two tokens (``--value secret``) or a single
    ``--value=secret`` token.

    Args:
        argv: Raw argv tokens (typically ``sys.argv`` or ``sys.argv[1:]``).
        mask: Replacement text for sensitive values.

    Returns:
        List[str]: A new list with sensitive values replaced by *mask*.
    """
    redacted: List[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            redacted.append(mask)
            redact_next = False
            continue

        flag = token.split("=", 1)[0].lower()
        if flag in _SENSITIVE_ARGV_FLAGS:
            if "=" in token:
                redacted.append(f"{token.split('=', 1)[0]}={mask}")
            else:
                redacted.append(token)
                redact_next = True
            continue

        redacted.append(token)

    return redacted


# Get the normalized path
def sanitize_filename(name: str) -> str:
    """Sanitize a string into a valid filename component matching PlatformName rules.

    Converts arbitrary text into a lowercase, alphanumeric-plus-hyphens-underscores
    string safe for use as a filename. Applies the same constraints as
    ``PlatformName``: ``^[a-z][a-z0-9_-]*$``, max 64 chars.

    Steps:
    1. Lowercase the input
    2. Replace path separators and invalid chars with underscores
    3. Collapse consecutive underscores
    4. Strip leading/trailing underscores, dots, and spaces
    5. If the result doesn't start with a letter, prefix with ``f``
    6. Truncate to 64 characters

    .. note::
        Currently unused in production — ``PlatformName`` validation on
        Pydantic models handles this at the boundary. Kept as a utility
        for future use where raw user input needs filename conversion
        without model validation (e.g. CLI scaffolding, export paths).

    Args:
        name: Arbitrary string to sanitize.

    Returns:
        A PlatformName-compatible filename string, or empty string if
        input is empty/whitespace-only.
    """
    if not name or not name.strip():
        return ""
    # Lowercase
    name = name.lower()
    # Replace anything not alphanumeric, underscore, or hyphen with underscore
    name = re.sub(r"[^a-z0-9_-]", "_", name)
    # Collapse consecutive underscores/hyphens
    name = re.sub(r"[_-]{2,}", "_", name)
    # Strip leading/trailing underscores, hyphens, dots, spaces
    name = name.strip("_-. ")
    # Must start with a letter
    if name and not name[0].isalpha():
        name = f"f{name}"
    # Truncate to PlatformName max length
    return name[:64]


# Join multiple paths
def resolve_path(
    base_path: str,
    target_path: Optional[str] = None,
    *sub_paths: str,
    repo_map: Optional[Dict[str, str]] = None,
) -> Path:
    """
    Resolve a path, optionally using cross-repo ``@repo-name/...`` references.

    Resolution rules (applied in order):

    1. **Cross-repo reference** — if ``target_path`` starts with ``@``, the
       token is split into ``@<repo_name>/<rest>``.  The repo root is looked
       up in ``repo_map`` and the rest of the path is appended.  If the repo
       name is not in ``repo_map`` (or ``repo_map`` is ``None``), a
       :class:`ValueError` is raised.
    2. **Absolute target** — if ``target_path`` is an absolute path, it is
       used as-is (``base_path`` is ignored).
    3. **Relative target** — ``target_path`` is joined onto ``base_path``.
    4. **Sub-paths** — any positional ``*sub_paths`` are appended.  Absolute
       values in ``sub_paths`` are rejected with :class:`ValueError`.
    5. **No target** — when ``target_path`` is ``None`` or empty, only
       ``base_path`` (plus any ``sub_paths``) is returned.

    If ``base_path`` is ``None`` or an empty string, the current working
    directory is used as the base.

    Args:
        base_path: Base directory path (fallback: CWD).
        target_path: Target path or ``@repo-name/relative/path`` reference.
        *sub_paths: Additional relative path segments to append.
        repo_map: Mapping of repo names to their root paths, required when
            ``target_path`` contains an ``@`` reference.

    Returns:
        Resolved :class:`pathlib.Path`.

    Raises:
        ValueError: Unknown ``@repo-name`` reference, or absolute path found
            in ``sub_paths``.
    """
    if base_path is None or base_path == "":
        base_path = os.getcwd()

    # Normalise to str — callers may pass Path objects
    base_path = str(base_path)
    if target_path is not None:
        target_path = str(target_path)

    # On non-Windows, backslashes are not path separators. A path authored on
    # Windows using '\' silently resolves to a single-component filename on
    # Linux, producing a confusing "file not found" error later. Catch it here.
    if os.name != "nt" and target_path and "\\" in target_path and "/" not in target_path:
        raise ValueError(
            f"Path '{target_path}' uses Windows backslash separators. "
            f"Use forward slashes (/) in YAML config files for cross-platform compatibility."
        )

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
       ``STRATA_WORK_PATH`` env var).  Returned as-is (resolved to absolute).
    2. Upward walk — starting from ``Path.cwd()``, walk up through parent
       directories until a ``.strata/`` directory is found.  The directory
       that contains ``.strata/`` is returned as the workspace root.
    3. CWD — fallback when no ``.strata/`` ancestor can be found.

    Args:
        explicit: Optional explicit path string.  Pass ``None`` or ``""`` to
                  trigger automatic discovery.

    Returns:
        Resolved :class:`pathlib.Path` for the workspace root.
    """
    if explicit:
        return Path(explicit).resolve()

    # Walk up from CWD looking for .strata/
    candidate = Path.cwd().resolve()
    while True:
        if (candidate / ".strata").is_dir():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    return Path.cwd().resolve()


# Get the root path of the package
def get_pkg_root_path() -> Path:
    """Get the root path of the strata package."""
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


# Get the path to the built-in help topics directory
def get_pkg_help_path() -> Path:
    """Get the path to the built-in help topics directory."""
    return get_pkg_data_path() / "help"


# Get the path to the built-in diagram definitions directory
def get_pkg_diagrams_path() -> Path:
    """Get the path to the shipped ``kind: diagram`` definitions directory."""
    return get_pkg_data_path() / "diagrams"


# Get the path to the default configuration file
def get_pkg_config_path() -> Path:
    """Get the path to the default configuration file."""
    return get_pkg_data_path() / "configuration.yaml"


# Get the path to the default logging configuration file
def get_pkg_logging_path() -> Path:
    """Get the path to the default logging configuration file."""
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
    timed_out: bool = False

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
    line_callback: Optional[Callable[[str, str], None]] = None,
    input: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> "CommandResult":
    """
    Run a shell command and capture its output.

    Args:
        command: The command to run (string or List of args).
        show_output: If True, prints the command and live output.
        check: If True, raise an exception if returncode != 0.
        timeout: Timeout in seconds for command execution.
        capture_output: If True, capture stdout/stderr. If False, allow interactive. If None, auto-detect based on show_output.
        cwd: Working directory for command execution.
        line_callback: Optional callable ``(stream, line) -> None`` called for every output
            line as it arrives.  *stream* is ``"stdout"`` or ``"stderr"``.  When set,
            the command is run with ``Popen`` for true streaming instead of
            ``subprocess.run``; ``show_output`` is ignored in this mode.
        input: Optional string to write to the process stdin before reading output.
            The value is never logged (safe for secret material). Requires the process
            to read stdin before producing significant output to avoid deadlock.
        env: Optional environment mapping for the subprocess. When ``None``, the
            current process environment (``os.environ.copy()``) is used.

    Returns:
        CommandResult object with returncode, stdout, stderr, command, duration_ms, and
        timed_out. Use result.is_successful to check if command succeeded.
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

    if show_output and line_callback is None:
        console.print(f"Running command: [yellow]{cmd_display}[/yellow]")

    # Auto-detect capture_output if not specified
    if capture_output is None:
        capture_output = True  # Default to capturing output

    # ------------------------------------------------------------------
    # Streaming path — used when a line_callback is supplied or when
    # show_output=True (so verbose output appears live, not buffered).
    # ------------------------------------------------------------------
    if line_callback is not None or show_output:
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        from strata.utils.shutdown_coordinator import deregister_process, register_process

        try:
            with subprocess.Popen(
                command,
                shell=isinstance(command, str),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if input is not None else None,
                text=True,
                env=env if env is not None else os.environ.copy(),
                cwd=cwd,
            ) as proc:
                register_process(proc)

                # Write stdin before drain threads start (safe for short inputs).
                # The value is never logged — callers may pass secret material here.
                if input is not None:
                    assert proc.stdin is not None
                    proc.stdin.write(input)
                    proc.stdin.close()

                def _drain(pipe: IO[str], stream_name: str, lines_acc: List[str]) -> None:
                    for raw in pipe:
                        line = raw.rstrip("\n\r")
                        lines_acc.append(line)
                        if line_callback is not None:
                            line_callback(stream_name, line)
                        elif show_output:
                            if stream_name == "stderr":
                                console.print(f"[red]{line}[/red]")
                            else:
                                console.print(f"[dim]{line}[/dim]")

                assert proc.stdout is not None
                assert proc.stderr is not None
                t_out = threading.Thread(target=_drain, args=(proc.stdout, "stdout", stdout_lines), daemon=True)
                t_err = threading.Thread(target=_drain, args=(proc.stderr, "stderr", stderr_lines), daemon=True)
                t_out.start()
                t_err.start()
                try:
                    if timeout is not None:
                        deadline = time.monotonic() + timeout
                        remaining = deadline - time.monotonic()
                        t_out.join(timeout=max(0, remaining))
                        remaining = deadline - time.monotonic()
                        t_err.join(timeout=max(0, remaining))
                        remaining = deadline - time.monotonic()
                        if remaining < 0 or t_out.is_alive() or t_err.is_alive():
                            raise subprocess.TimeoutExpired(cmd_display, timeout)
                        returncode = proc.wait(timeout=max(0, remaining))
                    else:
                        t_out.join()
                        t_err.join()
                        returncode = proc.wait()
                except subprocess.TimeoutExpired:
                    proc.kill()
                    t_out.join()
                    t_err.join()
                    returncode = proc.wait()
                finally:
                    deregister_process(proc)

            duration_ms = (time.time() - start_time) * 1000
            stdout_text = "\n".join(stdout_lines)
            stderr_text = "\n".join(stderr_lines)

            logger.debug(
                "Command completed (streaming)",
                command=cmd_display,
                returncode=returncode,
                duration_ms=round(duration_ms, 2),
            )

            if check and returncode != 0:
                logger.error(
                    "Command failed",
                    command=cmd_display,
                    returncode=returncode,
                    duration_ms=round(duration_ms, 2),
                    stderr=stderr_text.strip(),
                )
                raise subprocess.CalledProcessError(returncode, cmd_display, stdout_text, stderr_text)

            return CommandResult(
                returncode=returncode,
                stdout=stdout_text.strip(),
                stderr=stderr_text.strip(),
                command=cmd_display,
                duration_ms=round(duration_ms, 2),
            )

        except subprocess.CalledProcessError:
            raise
        except FileNotFoundError as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.debug("Command not found in PATH", command=cmd_display, error=str(e))
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"Command not found: {cmd_display}",
                command=cmd_display,
                duration_ms=round(duration_ms, 2),
            )

    # ------------------------------------------------------------------
    # Buffered path — Popen + communicate for SIGTERM parity with the
    # streaming path. The shutdown coordinator can now cancel buffered
    # commands on SIGTERM (ADR-0028 gap closed).
    # ------------------------------------------------------------------
    from strata.utils.shutdown_coordinator import deregister_process, register_process

    timed_out = False
    try:
        with subprocess.Popen(
            command,
            shell=isinstance(command, str),
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            stdin=subprocess.PIPE if input is not None else None,
            text=True,
            env=env if env is not None else os.environ.copy(),
            cwd=cwd,
        ) as proc:
            register_process(proc)
            try:
                stdout_data, stderr_data = proc.communicate(input=input, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_data, stderr_data = proc.communicate()
                timed_out = True
            finally:
                deregister_process(proc)
            returncode = proc.returncode

        if check and returncode != 0:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "Command failed",
                command=cmd_display,
                returncode=returncode,
                duration_ms=round(duration_ms, 2),
                stderr=(stderr_data or "").strip(),
            )
            raise subprocess.CalledProcessError(returncode, cmd_display, stdout_data, stderr_data)

        duration_ms = (time.time() - start_time) * 1000
        logger.debug(
            "Command completed",
            command=cmd_display,
            returncode=returncode,
            duration_ms=round(duration_ms, 2),
        )

        return CommandResult(
            returncode=returncode,
            stdout=(stdout_data or "").strip(),
            stderr=(stderr_data or "").strip(),
            command=cmd_display,
            duration_ms=round(duration_ms, 2),
            timed_out=timed_out,
        )

    except subprocess.CalledProcessError:
        raise
    except FileNotFoundError as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.debug(
            "Command not found in PATH",
            command=cmd_display,
            error=str(e),
        )
        return CommandResult(
            returncode=127,
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
