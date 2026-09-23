#!/usr/bin/env python3
"""Tests for the transport primitives — subprocess and HTTP (ADR-0021 D5).

Subprocess tests drive `sys.executable`, which is real, present on every
machine the suite runs on, and needs no mocking. HTTP tests stub
`requests.request`, so nothing here touches the network.
"""

import json
import sys

import pytest
import requests

from strata.utils.transport import (
    EXECUTABLE_NOT_FOUND,
    NO_RESPONSE,
    TIMED_OUT,
    CommandResult,
    HttpResult,
    TransportResult,
    http_request,
    run_command,
)


def _python(*code: str) -> list[str]:
    """Build an argv running `code` in this interpreter."""
    return [sys.executable, "-c", "; ".join(code)]


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------


def test_successful_command_reports_stdout_as_payload():
    result = run_command(_python("print('hello')"))
    assert result.is_successful
    assert result.payload.strip() == "hello"
    assert result.returncode == 0


def test_non_zero_exit_is_a_result_not_an_exception():
    """A failing tool is an outcome the caller decides about."""
    result = run_command(_python("raise SystemExit(3)"))
    assert not result.is_successful
    assert result.returncode == 3


def test_stderr_is_captured_separately_from_stdout():
    result = run_command(_python("import sys", "print('bad', file=sys.stderr)"))
    assert "bad" in result.stderr
    assert result.stdout.strip() == ""


def test_missing_executable_reports_127_without_raising():
    """A tool that is not installed must not crash the run."""
    result = run_command(["strata-definitely-not-a-real-binary"])
    assert not result.is_successful
    assert result.returncode == EXECUTABLE_NOT_FOUND
    assert "not found" in result.stderr


def test_empty_argv_is_handled():
    result = run_command([])
    assert not result.is_successful
    assert result.returncode == EXECUTABLE_NOT_FOUND


def test_timeout_sets_the_flag_and_keeps_partial_output():
    """Output written before the kill is usually what explains the hang."""
    result = run_command(
        _python("import sys, time", "print('started', flush=True)", "time.sleep(30)"),
        timeout=1,
    )
    assert result.timed_out
    assert not result.is_successful
    assert result.returncode == TIMED_OUT
    assert "started" in result.stdout


def test_cwd_is_honoured(tmp_path):
    result = run_command(_python("import os", "print(os.getcwd())"), cwd=tmp_path)
    assert result.is_successful
    assert str(tmp_path.resolve()) in result.payload


def test_env_overlays_the_current_environment_rather_than_replacing_it():
    """The regression this design exists to prevent.

    v1 required every call site to write `{**os.environ, ...}`; forgetting
    once emptied PATH and the tool then failed for an unrelated reason.
    """
    result = run_command(
        _python("import os", "print(os.environ.get('STRATA_PROBE'))", "print(bool(os.environ.get('PATH')))"),
        env={"STRATA_PROBE": "set-by-test"},
    )
    assert result.is_successful
    lines = result.payload.split()
    assert lines[0] == "set-by-test"
    assert lines[1] == "True", "PATH must survive an env overlay"


def test_undecodable_output_does_not_raise():
    """A Windows locale code page must not be able to kill a run."""
    result = run_command(_python("import sys", r"sys.stdout.buffer.write(b'\xff\xfe')"))
    assert result.is_successful  # replaced, not raised


# ---------------------------------------------------------------------------
# run_command — stdin (v1 evidence: 7 call sites — SSH keys, registry
# passwords — piped rather than passed as an argv value visible in ps/Task
# Manager)
# ---------------------------------------------------------------------------


def test_input_is_written_to_stdin():
    result = run_command(_python("import sys", "print(sys.stdin.read().strip())"), input="hunter2")
    assert result.is_successful
    assert result.payload.strip() == "hunter2"


def test_input_never_appears_in_the_command_result():
    """Secret material piped via stdin must not leak back out through stdout/stderr."""
    result = run_command(_python("import sys", "sys.stdin.read()", "print('done')"), input="top-secret-value")
    assert "top-secret-value" not in result.stdout
    assert "top-secret-value" not in result.stderr


# ---------------------------------------------------------------------------
# run_command — streaming (v1 evidence: 63 call sites across every deployer —
# a multi-minute `terraform apply`/`helm upgrade` must not buffer silently)
# ---------------------------------------------------------------------------


def test_line_callback_receives_each_line_as_it_arrives():
    lines: list[tuple[str, str]] = []
    result = run_command(
        _python("print('one')", "print('two')"),
        line_callback=lambda stream, line: lines.append((stream, line)),
    )
    assert result.is_successful
    assert ("stdout", "one") in lines
    assert ("stdout", "two") in lines


def test_line_callback_separates_stdout_and_stderr():
    lines: list[tuple[str, str]] = []
    result = run_command(
        _python("import sys", "print('out-line')", "print('err-line', file=sys.stderr)"),
        line_callback=lambda stream, line: lines.append((stream, line)),
    )
    assert result.is_successful
    assert ("stdout", "out-line") in lines
    assert ("stderr", "err-line") in lines


def test_streaming_result_matches_buffered_result_content():
    """The streaming path is a different code path — its CommandResult must agree."""
    result = run_command(
        _python("print('same')"),
        line_callback=lambda stream, line: None,
    )
    assert result.is_successful
    assert result.payload.strip() == "same"


def test_streaming_command_reports_non_zero_exit():
    result = run_command(
        _python("raise SystemExit(5)"),
        line_callback=lambda stream, line: None,
    )
    assert not result.is_successful
    assert result.returncode == 5


def test_streaming_timeout_sets_the_flag_and_keeps_partial_output():
    lines: list[tuple[str, str]] = []
    result = run_command(
        _python("import time", "print('started', flush=True)", "time.sleep(30)"),
        timeout=1,
        line_callback=lambda stream, line: lines.append((stream, line)),
    )
    assert result.timed_out
    assert not result.is_successful
    assert result.returncode == TIMED_OUT
    assert ("stdout", "started") in lines
    assert "started" in result.stdout


def test_streaming_accepts_input_on_stdin():
    lines: list[tuple[str, str]] = []
    result = run_command(
        _python("import sys", "print(sys.stdin.read().strip())"),
        input="piped-value",
        line_callback=lambda stream, line: lines.append((stream, line)),
    )
    assert result.is_successful
    assert ("stdout", "piped-value") in lines


# ---------------------------------------------------------------------------
# http_request
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def _stub_request(monkeypatch, response=None, error=None):
    """Replace `requests.request` with something that returns or raises."""

    def _fake(**kwargs):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(requests, "request", _fake)


def test_successful_response_reports_body_as_payload(monkeypatch):
    _stub_request(monkeypatch, _FakeResponse(200, '{"ok": true}'))
    result = http_request("GET", "https://example.test/v1/thing")
    assert result.is_successful
    assert result.payload == '{"ok": true}'
    assert result.status == 200


def test_error_status_keeps_both_the_status_and_the_body(monkeypatch):
    """An error body usually says what was wrong — discarding it loses that."""
    _stub_request(monkeypatch, _FakeResponse(404, '{"error": "no such key"}'))
    result = http_request("GET", "https://example.test/v1/missing")
    assert not result.is_successful
    assert result.status == 404
    assert "no such key" in result.body


def test_connection_failure_reports_no_response_rather_than_a_status(monkeypatch):
    """'There was no server' must never look like 'the server said no'."""
    _stub_request(monkeypatch, error=requests.exceptions.ConnectionError("refused"))
    result = http_request("GET", "https://example.test/")
    assert not result.is_successful
    assert result.status == NO_RESPONSE
    assert "refused" in result.body


def test_timeout_sets_the_flag(monkeypatch):
    _stub_request(monkeypatch, error=requests.exceptions.Timeout("too slow"))
    result = http_request("GET", "https://example.test/")
    assert result.timed_out
    assert not result.is_successful
    assert result.status == NO_RESPONSE


def test_json_parses_the_body(monkeypatch):
    _stub_request(monkeypatch, _FakeResponse(200, '{"value": "secret"}'))
    assert http_request("GET", "https://example.test/").json() == {"value": "secret"}


def test_json_raises_on_a_malformed_body(monkeypatch):
    """A broken server contract must not arrive disguised as 'not found'."""
    _stub_request(monkeypatch, _FakeResponse(200, "<html>gateway error</html>"))
    with pytest.raises(json.JSONDecodeError):
        http_request("GET", "https://example.test/").json()


def test_response_headers_are_lowercased(monkeypatch):
    """`requests` is case-insensitive; a plain dict of it would not be."""
    _stub_request(monkeypatch, _FakeResponse(200, "", {"Content-Type": "application/json"}))
    result = http_request("GET", "https://example.test/")
    assert result.headers["content-type"] == "application/json"


def test_request_is_passed_through_verbatim(monkeypatch):
    """Method, headers and body must reach the wire unchanged."""
    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(200, "")

    monkeypatch.setattr(requests, "request", _fake)
    http_request("POST", "https://example.test/x", headers={"Authorization": "Bearer t"}, body=b"payload")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://example.test/x"
    assert captured["headers"] == {"Authorization": "Bearer t"}
    assert captured["data"] == b"payload"


# ---------------------------------------------------------------------------
# The shared contract
# ---------------------------------------------------------------------------


def test_both_results_satisfy_the_transport_contract():
    """What lets a capability method stay transport-agnostic (D1)."""
    command: TransportResult = CommandResult(returncode=0, stdout="out", stderr="")
    http: TransportResult = HttpResult(status=200, body="body")

    assert command.is_successful and command.payload == "out"
    assert http.is_successful and http.payload == "body"
    assert isinstance(command, TransportResult)
    assert isinstance(http, TransportResult)
