#!/usr/bin/env python3
"""Talking to the outside world — subprocesses and HTTP, with one result shape.

The bottom of the integration layer (ADR-0021 D5). An integration may be
reached by running a CLI or by calling a service, and the *same* capability
method has to work either way — `consul kv get k` and `GET /v1/kv/k` resolve
the same value. What must therefore be uniform is the **result**, not the set
of protocols, which is why `TransportResult` is the contract and the two
functions below are merely the two cases common enough to ship.

**Neither function raises for a failed call.** A non-zero exit, a 404, a
refused connection and a timeout are all *outcomes* an integration decides
about, not exceptions to propagate: a `values get` that finds no secret
should report which key failed, not surface a `CalledProcessError`. So every
failure mode is folded into the returned object and `if not
result.is_successful` is the single check either way.

**A protocol not shipped here is implemented by the integration that needs
it.** There is deliberately no transport registry: an integration speaking
syslog opens its own socket and returns its own `TransportResult`. Promoting
a protocol into this module happens when a *second* integration needs it.

**Three things ported from v1's `run_command`, confirmed load-bearing by a
count of its real call sites, not guessed:** streaming output via
`line_callback` (63 call sites — every deployer forwards `terraform apply`/
`helm upgrade` output live, since buffering minutes of output would leave a
caller silent until the process exits); `input` for stdin (7 call sites —
secret material like an SSH private key or a registry password must never
appear in `args`, which is visible in `ps`/Task Manager, unlike stdin); and
killing the child if a wait is interrupted, so `Ctrl-C` cannot orphan a
process holding a state lock. Not ported: v1's process-wide
`shutdown_coordinator` (a signal-handler registry coordinating cleanup
across many concurrent commands) — this module's cleanup is scoped to one
call; build the coordinator if `deploy` ever runs more than one long-lived
child at once.

Deliberately silent: this module logs nothing. Where transport logging
belongs — and how it avoids writing secrets, given that a store's response
body *is* the secret — is an open question, and a wrong answer here would be
one that leaks. See ADR-0021.
"""

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import requests

#: Exit status for "command not found", following the POSIX shell convention.
#: Used when the executable cannot be resolved, so a missing tool reports the
#: same way a failing one does instead of raising.
EXECUTABLE_NOT_FOUND = 127

#: Exit status reported when the timeout fired, matching POSIX `timeout(1)`.
#: A killed process has no exit status of its own, but `is_successful` must
#: still be False on the returncode alone, for a caller that only looks there.
TIMED_OUT = 124

#: Status used when no HTTP response was received at all — DNS failure,
#: refused connection, TLS error. Distinct from any real status code, so
#: "the server said no" and "there was no server" never look alike.
NO_RESPONSE = 0

_DEFAULT_COMMAND_TIMEOUT = 60
_DEFAULT_REQUEST_TIMEOUT = 30


@runtime_checkable
class TransportResult(Protocol):
    """What every transport returns, whatever protocol it spoke.

    The whole contract: did it work, and what came back. A capability method
    can be written against this and stay transport-agnostic, which is what
    lets one integration class serve both a CLI-configured and an
    API-configured solution.

    `runtime_checkable` so a test can assert a third-party integration's own
    result type satisfies it — structural typing only checks method presence,
    which is enough for the "did you implement the contract" question.
    """

    @property
    def is_successful(self) -> bool:
        """True when the call did what was asked."""
        ...

    @property
    def payload(self) -> str:
        """What came back on success: stdout, or a response body."""
        ...


@dataclass(frozen=True)
class CommandResult:
    """The outcome of running a subprocess."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def is_successful(self) -> bool:
        """True on a zero exit that was not killed by the timeout.

        The timeout is checked separately because a process killed mid-run
        can still exit 0 on some platforms.
        """
        return self.returncode == 0 and not self.timed_out

    @property
    def payload(self) -> str:
        """Standard output."""
        return self.stdout


@dataclass(frozen=True)
class HttpResult:
    """The outcome of an HTTP request."""

    status: int
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timed_out: bool = False

    @property
    def is_successful(self) -> bool:
        """True for a 2xx that was not a timeout."""
        return 200 <= self.status < 300 and not self.timed_out

    @property
    def payload(self) -> str:
        """The response body."""
        return self.body

    def json(self) -> Any:
        """Parse the body as JSON.

        Raises rather than returning None on a malformed body: an
        unparseable response is a broken server contract, and swallowing it
        would let "the API is misbehaving" reach a caller disguised as "the
        key does not exist".

        Raises:
            json.JSONDecodeError: The body is not valid JSON.
        """
        return json.loads(self.body)


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = _DEFAULT_COMMAND_TIMEOUT,
    input: str | None = None,
    line_callback: Callable[[str, str], None] | None = None,
) -> CommandResult:
    """Run `args` and capture its output.

    Never invokes a shell. Arguments reach here from solution documents, so
    `shell=True` would make a crafted value executable; passing a list means
    the operating system treats every element as one argument no matter what
    it contains.

    The executable is resolved with `shutil.which()` first, for two reasons:
    it makes a missing tool a result rather than an exception, and on Windows
    the real file is often `az.cmd`/`helm.exe` rather than the bare name,
    which `subprocess` without a shell would not find.

    Args:
        args: Executable and its arguments. Never a single string.
        cwd: Working directory. Defaults to the current one.
        env: Variables to **add to or override in** the current environment
            — not to replace it. v1 passed a complete environment and so had
            to write `{**os.environ, ...}` at every call site; forgetting once
            empties `PATH` and the tool then fails for a reason unrelated to
            what the caller did.
        timeout: Seconds before the process is killed.
        input: Text written to the process's stdin before its output is read.
            Never logged and never appears in `args` — used for secret
            material an integration must not expose on the command line
            (visible in `ps`/Task Manager, unlike stdin), e.g. an SSH private
            key or a registry password.
        line_callback: When given, called as `(stream, line)` for every line
            of output as it arrives (`stream` is `"stdout"` or `"stderr"`),
            and the process is run in streaming mode instead of buffered.
            Needed for a provisioner that runs for minutes (`terraform
            apply`, `helm upgrade`) — buffering the whole run would leave a
            caller silent until the process exits.

    Returns:
        The outcome, including partial output when the timeout fired.
    """
    if not args:
        return CommandResult(returncode=EXECUTABLE_NOT_FOUND, stdout="", stderr="no command given")

    executable = shutil.which(args[0])
    if executable is None:
        return CommandResult(
            returncode=EXECUTABLE_NOT_FOUND,
            stdout="",
            stderr=f"executable not found on PATH: {args[0]}",
        )

    merged_env = {**os.environ, **(env or {})}

    try:
        process = subprocess.Popen(
            [executable, *args[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input is not None else None,
            cwd=cwd,
            env=merged_env,
            # Explicit rather than inherited: the Windows default is the
            # locale code page, so one non-ASCII byte in a tool's output
            # would raise UnicodeDecodeError and lose the whole run. Errors
            # are replaced because unreadable output is still diagnostic.
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        # Resolved but unrunnable: no execute permission, broken symlink,
        # wrong architecture.
        return CommandResult(returncode=EXECUTABLE_NOT_FOUND, stdout="", stderr=str(error))

    try:
        if line_callback is not None:
            return _run_streaming(process, input=input, timeout=timeout, line_callback=line_callback)
        return _run_buffered(process, input=input, timeout=timeout)
    except BaseException:
        # Cleanup for *this* call only — if anything above raises (most
        # plausibly KeyboardInterrupt reaching in through a join/communicate
        # wait), the child must not be left running. This is local, not v1's
        # process-wide shutdown_coordinator (a signal-handler registry
        # coordinating cleanup across many concurrent commands); build that
        # if `deploy` ever runs more than one long-lived child at once.
        process.kill()
        process.wait()
        raise


def _run_buffered(process: subprocess.Popen[str], *, input: str | None, timeout: int) -> CommandResult:
    """Run to completion, returning only once all output is collected."""
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
        return CommandResult(returncode=process.returncode, stdout=stdout or "", stderr=stderr or "")
    except subprocess.TimeoutExpired:
        # Per the stdlib's own recommendation: communicate() does not kill
        # the child on timeout, so a well-behaved caller does — then drains
        # whatever output remains before returning.
        process.kill()
        stdout, stderr = process.communicate()
        return CommandResult(returncode=TIMED_OUT, stdout=stdout or "", stderr=stderr or "", timed_out=True)


def _run_streaming(
    process: subprocess.Popen[str],
    *,
    input: str | None,
    timeout: int | None,
    line_callback: Callable[[str, str], None],
) -> CommandResult:
    """Run while forwarding each line of output to `line_callback` as it arrives."""
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    if input is not None:
        assert process.stdin is not None
        process.stdin.write(input)
        process.stdin.close()

    def _drain(pipe: Any, stream_name: str, lines: list[str]) -> None:
        for raw in pipe:
            line = raw.rstrip("\r\n")
            lines.append(line)
            line_callback(stream_name, line)

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(target=_drain, args=(process.stdout, "stdout", stdout_lines), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, "stderr", stderr_lines), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    try:
        if timeout is not None:
            deadline = time.monotonic() + timeout
            stdout_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            stderr_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                raise subprocess.TimeoutExpired(process.args, timeout)
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        else:
            stdout_thread.join()
            stderr_thread.join()
            returncode = process.wait()
    except subprocess.TimeoutExpired:
        process.kill()
        stdout_thread.join()
        stderr_thread.join()
        process.wait()
        return CommandResult(
            returncode=TIMED_OUT,
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
            timed_out=True,
        )

    return CommandResult(returncode=returncode, stdout="\n".join(stdout_lines), stderr="\n".join(stderr_lines))


def http_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = _DEFAULT_REQUEST_TIMEOUT,
) -> HttpResult:
    """Perform an HTTP request and capture the response.

    A 4xx or 5xx is a normal return, not an exception — the body of an error
    response usually says what was wrong, and discarding it to raise would
    throw away the only useful part.

    TLS verification is deliberately not a parameter. A per-call switch is
    disabled once for a stubborn endpoint and then spreads by copy-paste; a
    private certificate authority belongs in the trust store or
    `REQUESTS_CA_BUNDLE`, where it applies deliberately and everywhere.

    Args:
        method: HTTP verb, e.g. `GET`.
        url: Absolute URL.
        headers: Request headers.
        body: Raw request body.
        timeout: Seconds before giving up, applied to both connect and read.

    Returns:
        The outcome. `status` is `NO_RESPONSE` when no response arrived at
        all, with the reason in `body`.
    """
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=dict(headers) if headers else None,
            data=body,
            timeout=timeout,
        )
    except requests.exceptions.Timeout as expired:
        return HttpResult(status=NO_RESPONSE, body=str(expired), timed_out=True)
    except requests.exceptions.RequestException as error:
        # Connection refused, DNS failure, TLS error, too many redirects:
        # there is no status because there was no response.
        return HttpResult(status=NO_RESPONSE, body=str(error))

    return HttpResult(
        status=response.status_code,
        body=response.text,
        # Lowercased because `requests` returns a case-insensitive mapping
        # whose case-sensitivity would silently return on the way out:
        # `headers["content-type"]` would miss a `Content-Type` header.
        headers={name.lower(): value for name, value in response.headers.items()},
    )
