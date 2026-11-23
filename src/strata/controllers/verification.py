#!/usr/bin/env python3
"""Post-deploy verification: run a `verify` block's checks (a stage's after its step is applied, the deployment's
`spec.verify` after every step, or either on its own under `deploy verify`), and decide what the answers mean for the
run (docs/design/post-deploy-verification.md, ADR-0066).

A check answers **pass, warn, fail or unknown**:

- a `script` check's exit code gives the answer: `binary` (default) reads 0 as pass and anything else as fail;
  `monitoring` (the Nagios/Icinga plugin convention) reads 0 pass, 1 warn, 2 fail, 3 unknown, anything else fail;
  an attempt that runs out of `timeout` is unknown and is retried;
- an `http` check passes when the status is in `expect_status`, unless an `application/health+json` body's
  `status` says otherwise (`pass`, `warn`, `fail`, IETF draft); no answer at all (refused, timed out) fails;
- a `tcp` check passes when the connection opens;
- a `helm` check runs the chart's own tests (`helm test`) once: 0 passes, anything else fails;
- `expect: down` turns `http` and `tcp` around, for a check after a destroy: no answer passes;
- a check that cannot start (no such executable, a token that does not resolve, not an http(s) URL) is unknown and
  is not retried, since retrying will not make it appear.

A check runs for the actions in its `when` (D6): `apply` after a deploy's step and under `deploy verify`, `destroy`
after a destroy's.

Timing follows Kubernetes probes (D4): the block's `initial_delay` is waited once, then each check runs up to
`failure_threshold` attempts `period` seconds apart (start to start) until `success_threshold` consecutive passes.
A warn ends the attempts at once: a degraded answer is an answer. The stage's `timeouts.verify` bounds the whole
phase when set.

The reaction uses the gates' vocabulary (D1): a failed check is an error under `enforcement: deny` and a warning
under `warn`; an unknown one follows `on_unknown` (`block`, `warn`, `skip`), like a gate's `on_missing_data`.
"""

import http.client
import json
import os
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any, Literal

from strata.models.audit_manifest_model import ManifestCheckModel
from strata.models.deployment_model import DeploymentVerifyCheckModel, DeploymentVerifyModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import run_command
from strata.utils.value_tokens import extract_value_tokens, resolve_value_tokens

Answer = Literal["pass", "warn", "fail", "unknown"]
#: `STRATA_ACTION`: `apply` after a deploy's step, `destroy` after a destroy's, `verify` under the stand-alone
#: `deploy verify` (which runs the `apply` checks).
Action = Literal["apply", "destroy", "verify"]

#: Exit code -> answer under `exit_codes: monitoring`; any other code fails.
_MONITORING: dict[int, Answer] = {0: "pass", 1: "warn", 2: "fail", 3: "unknown"}
#: A check's message is its first output line, cut to this length.
_MESSAGE_LIMIT = 300
#: Seconds a `helm` check's process gets beyond `helm test --timeout`, to report the tests.
_HELM_GRACE = 15
#: How much of an `http` answer's body is read, for an `application/health+json` status.
_BODY_LIMIT = 64 * 1024
#: An `application/health+json` `status` -> answer; the draft's aliases (`ok`/`up`, `error`/`down`) included.
_HEALTH_STATUS: dict[str, Answer] = {
    "pass": "pass",
    "ok": "pass",
    "up": "pass",
    "warn": "warn",
    "fail": "fail",
    "error": "fail",
    "down": "fail",
}


@dataclass(frozen=True)
class CheckResult:
    """One check's final answer: what the manifest records and the reaction reads."""

    name: str
    answer: Answer
    attempts: int
    duration_seconds: float
    message: str | None


@dataclass(frozen=True)
class _Attempt:
    answer: Answer
    message: str | None
    retry: bool


@dataclass(frozen=True)
class _Timing:
    initial_delay: int
    period: int
    timeout: int
    success_threshold: int
    failure_threshold: int


def run_verify_block(
    block: DeploymentVerifyModel,
    *,
    deployment: str,
    step: str | None,
    tokens: Mapping[str, str],
    root: Path,
    redact: Callable[[str], str] = lambda text: text,
    phase_timeout: int | None = None,
    on_step: Callable[[str], None] | None = None,
    line_callback: Callable[[str, str], None] | None = None,
    action: Action = "apply",
    wait: bool = True,
    environment: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> list[CheckResult]:
    """Run every enabled check of `block` in order, and return their final answers.

    Args:
        block: A stage's `verify` block, or the deployment's `spec.verify`.
        deployment: The deployment's name, for `STRATA_DEPLOYMENT`.
        step: The stage's step, for `STRATA_STEP` and the progress lines; `None` for `spec.verify`, whose checks
            get an empty `STRATA_STEP`.
        tokens: The values `${...}` tokens in `run` and `env` resolve from.
        root: The solution root: the working directory, and where a relative executable path is found.
        redact: Masks secret values in anything shown or recorded.
        phase_timeout: The stage's `timeouts.verify`: the whole phase stops when it runs out.
        on_step: Receives one progress line per attempt that did not pass, and per final answer.
        line_callback: Receives the commands' output lines as they arrive (`--follow`).
        action: `STRATA_ACTION`, and which checks run (their `when`): `apply` in a deploy, `destroy` in a destroy,
            `verify` (the `apply` checks) under `deploy verify`.
        wait: Wait the `initial_delay`s. `deploy verify` does not: nothing was just started.
        environment: Variables every `script` and `helm` check's command gets, before the check's own `env`: the
            deployment's target contexts (`HELM_KUBECONTEXT`, `DOCKER_CONTEXT`, ...), so a check runs against the
            cluster or engine its steps deployed to.
        sleep: Waits between attempts; `time.sleep`, looked up when called so a test can replace it.
        clock: A monotonic clock; `time.monotonic` likewise.
    """
    sleep = sleep if sleep is not None else time.sleep
    clock = clock if clock is not None else time.monotonic
    subject = _subject(step)

    def _step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    checks = _checks_for(block, action)
    values = dict(tokens)  # once: every attempt of every check only reads it
    deadline = clock() + phase_timeout if phase_timeout is not None else None
    if checks and wait and block.initial_delay:
        _step(f"verify {subject}: waiting {block.initial_delay}s before the first check")
        _wait(block.initial_delay, deadline, sleep, clock)
    results: list[CheckResult] = []
    for check in checks:
        result = _run_check(
            check,
            _timing(block, check, wait=wait),
            deployment=deployment,
            step=step,
            values=values,
            root=root,
            redact=redact,
            deadline=deadline,
            on_step=_step,
            line_callback=line_callback,
            action=action,
            environment=environment or {},
            sleep=sleep,
            clock=clock,
        )
        detail = f": {result.message}" if result.message else ""
        _step(f"verify {subject}: check '{check.name}' {result.answer} after {result.attempts} attempt(s){detail}")
        results.append(result)
    return results


def verify_diagnostics(
    block: DeploymentVerifyModel, results: list[CheckResult], *, step: str | None, action: Action = "apply"
) -> Diagnostics:
    """The findings for `results`, by the block's `enforcement` and `on_unknown` (D1, D7), located at `step`; a
    `spec.verify` finding (`step` is `None`) has no location, so it fails the run without failing a stage."""
    diagnostics = Diagnostics()
    label = f"Step '{step}'" if step is not None else "Deployment"
    if action == "destroy":
        done = "The step was destroyed" if step is not None else "Every step was destroyed"
        consequence = f"{done}, but the check says what it removed is not gone."
        fix = (
            "Find what the destroy left behind (in the tool's state, or created outside it) and remove it, or "
            "correct the check"
        )
    else:
        done = "The step was applied" if step is not None else "Every step was applied"
        consequence = f"{done}, but the deployment does not work as checked."
        fix = "Fix the deployed workload (its configuration, image or dependencies) and deploy again"
    for result in results:
        attempts = f"after {result.attempts} attempt(s)"
        detail = f": {result.message}" if result.message else ""
        if result.answer == "warn":
            diagnostics.warning(
                f"{label}: verify check '{result.name}' answered warn {attempts}{detail}.",
                location=step,
                code="verify_check_warning",
            )
        elif result.answer == "fail":
            report = diagnostics.error if block.enforcement == "deny" else diagnostics.warning
            report(
                f"{label}: verify check '{result.name}' failed {attempts}{detail}. {consequence}",
                location=step,
                code="verify_check_failed",
                hint=(
                    f"{fix}; while the check is new, `enforcement: warn` reports it without failing the run."
                    if block.enforcement == "deny"
                    else f"{fix}, then set `enforcement: deny` so a failure stops the run."
                ),
            )
        elif result.answer == "unknown":
            message = f"{label}: verify check '{result.name}' could not answer {attempts}{detail}."
            if block.on_unknown == "skip":
                diagnostics.info(message, location=step, code="verify_check_unknown")
            else:
                report = diagnostics.error if block.on_unknown == "block" else diagnostics.warning
                report(
                    message,
                    location=step,
                    code="verify_check_unknown",
                    hint="Make the check runnable (its executable on PATH or its URL valid, its tokens declared, a "
                    "`timeout` long enough); `on_unknown: warn` reports it without failing the run.",
                )
    return diagnostics


@dataclass
class VerifyReport:
    """What `deploy verify` found, in the manifest's shape (D9): each step's checks, then the deployment's."""

    stages: dict[str, list[ManifestCheckModel]] = field(default_factory=dict)
    checks: list[ManifestCheckModel] = field(default_factory=list)

    @property
    def verified(self) -> bool | None:
        """As the manifest's `verified`: see `all_verified()`."""
        return all_verified([*(check for checks in self.stages.values() for check in checks), *self.checks])

    def to_dict(self) -> dict[str, Any]:
        """The `data` of `deploy verify --output json`."""
        return {
            "verified": self.verified,
            "stages": [
                {"name": step, "checks": [check.model_dump(exclude_none=True) for check in checks]}
                for step, checks in self.stages.items()
            ],
            "checks": [check.model_dump(exclude_none=True) for check in self.checks],
        }


def all_verified(checks: Iterable[ManifestCheckModel]) -> bool | None:
    """`None` when no check ran, `True` when every one answered pass or warn, `False` when one failed or could not
    answer, whatever its `enforcement` or `on_unknown` made of it: 'verified' means the checks said so."""
    answers = [check.answer for check in checks]
    if not answers:
        return None
    return all(answer in ("pass", "warn") for answer in answers)


def has_checks(block: DeploymentVerifyModel | None, action: Action) -> bool:
    """Whether `block` is enabled and has a check that runs for `action`."""
    return block is not None and block.enabled and bool(_checks_for(block, action))


def output_steps(blocks: Iterable[DeploymentVerifyModel], action: Action = "verify") -> set[str]:
    """The steps whose outputs the checks of `blocks` that run for `action` read (`${output:STEP.KEY}`)."""
    steps: set[str] = set()
    for block in blocks:
        if not block.enabled:
            continue  # a block that is off runs nothing, so its outputs must not be read (or block a destroy)
        for check in _checks_for(block, action):
            for _, value in check.token_fields():
                steps.update(
                    key.partition(".")[0]
                    for kind, key in extract_value_tokens(value)
                    if kind == "output" and "." in key
                )
    return steps


def check_records(results: list[CheckResult]) -> list[ManifestCheckModel]:
    """`results` as the manifest and `deploy verify`'s JSON record them (D9)."""
    return [
        ManifestCheckModel(
            name=result.name,
            answer=result.answer,
            attempts=result.attempts,
            duration_seconds=result.duration_seconds,
            message=result.message,
        )
        for result in results
    ]


def would_verify(block: DeploymentVerifyModel, step: str | None, action: Action = "apply") -> list[str]:
    """The progress lines a `--dry-run` or `--smoke-test` reports instead of running the checks."""
    return [f"would verify {_subject(step)}: check '{check.name}'" for check in _checks_for(block, action)]


def _checks_for(block: DeploymentVerifyModel, action: Action) -> list[DeploymentVerifyCheckModel]:
    """The enabled checks of `block` whose `when` covers `action`."""
    return [check for check in block.checks if check.enabled and check.runs_on(action)]


def _subject(step: str | None) -> str:
    """What the progress lines name: the stage's step, or the deployment for `spec.verify`."""
    return f"step '{step}'" if step is not None else "the deployment"


def _timing(block: DeploymentVerifyModel, check: DeploymentVerifyCheckModel, *, wait: bool = True) -> _Timing:
    """A check's own timing fields where set, the block's otherwise. `initial_delay` is the check's alone: the
    block's was waited once, before the first check; without `wait` there is none."""
    return _Timing(
        initial_delay=(check.initial_delay or 0) if wait else 0,
        period=check.period if check.period is not None else block.period,
        timeout=check.timeout if check.timeout is not None else block.timeout,
        success_threshold=check.success_threshold if check.success_threshold is not None else block.success_threshold,
        failure_threshold=check.failure_threshold if check.failure_threshold is not None else block.failure_threshold,
    )


def _run_check(
    check: DeploymentVerifyCheckModel,
    timing: _Timing,
    *,
    deployment: str,
    step: str | None,
    values: dict[str, str],
    root: Path,
    redact: Callable[[str], str],
    deadline: float | None,
    on_step: Callable[[str], None],
    line_callback: Callable[[str, str], None] | None,
    action: Action,
    environment: Mapping[str, str],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> CheckResult:
    """Attempt `check` until `success_threshold` passes in a row, `failure_threshold` fails or unknowns in a row,
    an answer that cannot change (`retry=False`, whatever it is), a `warn`, or the deadline.

    A check that keeps flipping between pass and fail would reach neither threshold, so the attempts are capped at
    `failure_threshold + success_threshold - 1`: the most a check needs that fails up to the limit and then passes
    enough times. Hitting the cap on a pass answers fail: the workload did not stay up.
    """
    started = clock()
    if timing.initial_delay:
        _wait(timing.initial_delay, deadline, sleep, clock)
    attempts = passes = failures = 0
    max_attempts = timing.failure_threshold + timing.success_threshold - 1
    last = _Attempt("unknown", "the verify phase timed out before the check ran", retry=False)
    while deadline is None or clock() < deadline:
        attempt_started = clock()
        attempts += 1
        attempt_timeout = timing.timeout
        if deadline is not None:
            attempt_timeout = max(1, min(attempt_timeout, int(deadline - attempt_started)))
        last = _attempt(
            check,
            timeout=attempt_timeout,
            deployment=deployment,
            step=step,
            attempt=attempts,
            values=values,
            root=root,
            redact=redact,
            line_callback=line_callback,
            action=action,
            environment=environment,
        )
        if last.answer == "pass":
            passes += 1
            failures = 0
            # `helm test` passes once: running it again would only start its test pods again.
            if passes >= timing.success_threshold or not last.retry:
                break
        elif last.answer == "warn":
            break
        else:
            passes = 0
            failures += 1
            detail = f": {last.message}" if last.message else ""
            on_step(f"verify {_subject(step)}: check '{check.name}' attempt {attempts} {last.answer}{detail}")
            if not last.retry or failures >= timing.failure_threshold:
                break
        if attempts >= max_attempts:
            # Neither threshold was reached in the attempts a clean start needs: the answer keeps changing.
            latest = f" (last: {last.answer}{f', {last.message}' if last.message else ''})"
            last = _Attempt(
                "fail",
                f"no {timing.success_threshold} passes in a row in {attempts} attempts: the answer keeps "
                f"changing{latest}",
                retry=False,
            )
            break
        _wait(timing.period - (clock() - attempt_started), deadline, sleep, clock)
    else:
        if attempts and last.answer == "pass":
            last = _Attempt("unknown", "the verify phase timed out before enough passes", retry=False)
    return CheckResult(
        name=check.name,
        answer=last.answer,
        attempts=attempts,
        duration_seconds=round(clock() - started, 3),
        message=last.message,
    )


def _attempt(
    check: DeploymentVerifyCheckModel,
    *,
    timeout: int,
    deployment: str,
    step: str | None,
    attempt: int,
    values: dict[str, str],
    root: Path,
    redact: Callable[[str], str],
    line_callback: Callable[[str, str], None] | None,
    action: Action,
    environment: Mapping[str, str],
) -> _Attempt:
    if check.type == "http":
        return _http_attempt(check, timeout=timeout, values=values, root=root, redact=redact)
    if check.type == "tcp":
        return _tcp_attempt(check, timeout=timeout, values=values, redact=redact)
    try:
        if check.type == "helm":
            args = _helm_test_args(check, values, timeout)
        else:
            args = [resolve_value_tokens(argument, values) for argument in check.run or []]
        env = {
            **environment,
            **{key: resolve_value_tokens(value, values) for key, value in (check.env or {}).items()},
        }
    except ValueError as exc:
        return _Attempt("unknown", redact(str(exc)), retry=False)
    executable = _executable(args[0], root)
    if executable is None:
        return _Attempt(
            "unknown",
            redact(f"'{args[0]}' cannot be run: not found on PATH or relative to the solution root, or not executable"),
            retry=False,
        )
    env.update(
        STRATA_DEPLOYMENT=deployment,
        STRATA_STEP=step or "",
        STRATA_CHECK=check.name,
        STRATA_ACTION=action,
        STRATA_ATTEMPT=str(attempt),
    )

    def _line(stream: str, text: str) -> None:
        if line_callback is not None:
            line_callback(stream, redact(text))

    helm = check.type == "helm"
    result = run_command(
        [executable, *args[1:]],
        cwd=root,
        env=env,
        # `helm test --timeout` bounds the test pods; the process gets a little longer to report them.
        timeout=timeout + _HELM_GRACE if helm else timeout,
        line_callback=_line if line_callback is not None else None,
    )
    if helm:
        return _helm_answer(args[2], result.returncode, result.timed_out, result.stdout, result.stderr, redact, timeout)
    message = _first_line(result.stdout) or _first_line(result.stderr)
    message = redact(message)[:_MESSAGE_LIMIT] if message else None
    if result.timed_out:
        return _Attempt("unknown", message or f"timed out after {timeout}s", retry=True)
    if check.exit_codes == "monitoring":
        answer: Answer = _MONITORING.get(result.returncode, "fail")
    else:
        answer = "pass" if result.returncode == 0 else "fail"
    if answer != "pass" and message is None:
        message = f"exit code {result.returncode}"
    return _Attempt(answer, message, retry=True)


def _helm_test_args(check: DeploymentVerifyCheckModel, values: dict[str, str], timeout: int) -> list[str]:
    """`helm test RELEASE --namespace NS --timeout Ns [--filter name=TEST ...]`. The kube context comes from the
    deployment (`HELM_KUBECONTEXT` in `environment`), as for its Helm steps.

    Raises:
        ValueError: a token in `release` or `namespace` does not resolve.
    """
    args = ["helm", "test", resolve_value_tokens(check.release or "", values)]
    if check.namespace is not None:
        args += ["--namespace", resolve_value_tokens(check.namespace, values)]
    args += ["--timeout", f"{timeout}s"]
    for name in check.tests or []:
        args += ["--filter", f"name={name}"]
    return args


def _helm_answer(
    release: str,
    returncode: int,
    timed_out: bool,
    stdout: str,
    stderr: str,
    redact: Callable[[str], str],
    timeout: int,
) -> _Attempt:
    """`helm test`'s exit code: 0 passes, anything else fails. Never retried: Helm waited for the release to be
    ready before the tests ran, and each attempt starts the test pods again."""
    if timed_out:
        return _Attempt("unknown", f"helm test did not finish within {timeout}s", retry=False)
    if returncode == 0:
        return _Attempt("pass", redact(f"helm test {release}: the chart's tests passed"), retry=False)
    lines = [line.strip() for line in (stderr or stdout).splitlines() if line.strip()]
    detail = next((line for line in reversed(lines) if line.lower().startswith("error")), lines[-1] if lines else "")
    message = f"helm test {release} failed" + (f": {detail}" if detail else f" (exit code {returncode})")
    return _Attempt("fail", redact(message)[:_MESSAGE_LIMIT], retry=False)


def _http_attempt(
    check: DeploymentVerifyCheckModel,
    *,
    timeout: int,
    values: dict[str, str],
    root: Path,
    redact: Callable[[str], str],
) -> _Attempt:
    """One request (D10). The status decides, or an `application/health+json` body's `status` when there is one;
    no answer fails (`expect: up`) or passes (`expect: down`). A certificate that is not trusted is an answer from
    something, so it fails either way."""
    try:
        url = resolve_value_tokens(check.url or "", values)
        headers = {key: resolve_value_tokens(value, values) for key, value in (check.headers or {}).items()}
        data = None if check.body is None else resolve_value_tokens(check.body, values).encode("utf-8")
    except ValueError as exc:
        return _Attempt("unknown", redact(str(exc)), retry=False)
    shown = redact(url)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return _Attempt("unknown", f"'{shown}' is not an http:// or https:// URL", retry=False)
    try:
        context = _tls_context(check, root)
    except (OSError, ssl.SSLError) as error:
        return _Attempt("unknown", redact(f"ca_file '{check.ca_file}' cannot be read: {error}"), retry=False)
    unchecked = " (certificate not checked: insecure)" if check.insecure and parts.scheme == "https" else ""
    expected = check.expected_statuses()
    request = urllib.request.Request(url, data=data, method=check.method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310 - scheme checked
            status, content_type, body = response.status, response.headers, response.read(_BODY_LIMIT)
    except urllib.error.HTTPError as error:
        status, content_type = error.code, error.headers
        try:
            body = error.read(_BODY_LIMIT)
        except OSError:
            body = b""
    except (OSError, http.client.HTTPException, ValueError) as error:
        reason = getattr(error, "reason", None) or error or type(error).__name__
        if isinstance(reason, ssl.SSLCertVerificationError):
            detail = reason.verify_message or str(reason)
            message = f"the certificate of {shown} is not trusted: {detail}; set 'ca_file' to trust its CA"
            return _Attempt("fail", redact(message)[:_MESSAGE_LIMIT], retry=True)
        message = redact(f"no answer from {shown}: {reason}")[:_MESSAGE_LIMIT]
        if check.expect == "down":
            return _Attempt("pass", message, retry=True)
        return _Attempt("fail", message, retry=True)
    seen = f"HTTP {status} from {shown}{unchecked}"
    if check.expect == "down":
        if status in expected:
            return _Attempt("fail", redact(f"{seen}: it still answers")[:_MESSAGE_LIMIT], retry=True)
        return _Attempt("pass", redact(seen)[:_MESSAGE_LIMIT], retry=True)
    health = _health_status(content_type, body)
    if health is not None:
        answer, detail = health
        return _Attempt(answer, redact(f"{seen}, health {detail}")[:_MESSAGE_LIMIT], retry=True)
    if status in expected:
        return _Attempt("pass", redact(seen)[:_MESSAGE_LIMIT], retry=True)
    wanted = " or ".join(str(code) for code in expected)
    return _Attempt("fail", redact(f"{seen}, expected {wanted}")[:_MESSAGE_LIMIT], retry=True)


def _tls_context(check: DeploymentVerifyCheckModel, root: Path) -> ssl.SSLContext | None:
    """The TLS settings of an `http` check: `None` for the system's trust store; with `ca_file` only that file's
    certificate authorities are trusted; `insecure` checks nothing."""
    if check.insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if check.ca_file is None:
        return None
    path = Path(check.ca_file)
    return ssl.create_default_context(cafile=str(path if path.is_absolute() else root / path))


def _health_status(headers: Message, body: bytes) -> tuple[Answer, str] | None:
    """An `application/health+json` body's answer and its `status` (with `output`, when given); `None` for any other
    body, or one without a known `status`."""
    if headers.get_content_type() != "application/health+json":
        return None
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("status"), str):
        return None
    status = document["status"].strip().lower()
    answer = _HEALTH_STATUS.get(status)
    if answer is None:
        return None
    output = document.get("output")
    return answer, f"{status}: {output}" if isinstance(output, str) and output.strip() else status


def _tcp_attempt(
    check: DeploymentVerifyCheckModel, *, timeout: int, values: dict[str, str], redact: Callable[[str], str]
) -> _Attempt:
    """One connection (D10): it opens (`expect: up` passes) or it does not (`expect: down` passes)."""
    try:
        host = resolve_value_tokens(check.host or "", values)
    except ValueError as exc:
        return _Attempt("unknown", redact(str(exc)), retry=False)
    target = f"{host}:{check.port}"
    try:
        with socket.create_connection((host, check.port or 0), timeout=timeout):
            pass
    except OSError as error:
        message = redact(f"no connection to {target}: {error or type(error).__name__}")[:_MESSAGE_LIMIT]
        return _Attempt("pass" if check.expect == "down" else "fail", message, retry=True)
    if check.expect == "down":
        return _Attempt("fail", redact(f"connected to {target}: it still answers")[:_MESSAGE_LIMIT], retry=True)
    return _Attempt("pass", redact(f"connected to {target}")[:_MESSAGE_LIMIT], retry=True)


def _executable(name: str, root: Path) -> str | None:
    """`name` as found on PATH, or as a path relative to the solution root (one with a directory part); `None` when
    there is none, or the file cannot be executed (a `.ps1` or `.py` is run through its interpreter: `[pwsh, -File,
    checks/x.ps1]`)."""
    if os.sep in name or "/" in name:
        candidate = Path(name) if Path(name).is_absolute() else root / name
        return shutil.which(str(candidate))
    return shutil.which(name)


def _first_line(text: str) -> str | None:
    return next((line.strip() for line in text.splitlines() if line.strip()), None)


def _wait(seconds: float, deadline: float | None, sleep: Callable[[float], None], clock: Callable[[], float]) -> None:
    if deadline is not None:
        seconds = min(seconds, deadline - clock())
    if seconds > 0:
        sleep(seconds)
