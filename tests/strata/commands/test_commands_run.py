#!/usr/bin/env python3
"""Tests for the command lifecycle."""

import io
import json

import click
import pytest

from strata.commands.exit_codes import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.commands.run import command_run
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import StrataError, UsageError, ValidationError


def _capture(output="console", **kwargs):
    """Run a lifecycle, returning (exit_code, text) without exiting the test."""
    stream = io.StringIO()
    kwargs.setdefault("quiet", False)

    def run_it(body):
        try:
            with command_run("demo", output=output, **kwargs) as run:
                run.reporter.stream = stream  # type: ignore[attr-defined]
                body(run)
        except click.exceptions.Exit as exit_signal:
            return exit_signal.exit_code, stream.getvalue()
        raise AssertionError("command_run must always raise Exit")

    return run_it


# ---------------------------------------------------------------------------
# Always exits
# ---------------------------------------------------------------------------


def test_success_exits_zero():
    """A command body never has to remember to set an exit code."""
    code, _ = _capture()(lambda run: None)
    assert code == EXIT_SUCCESS


def test_not_ok_exits_with_the_failure_code():
    """The command states the outcome; the lifecycle renders it."""
    code, _ = _capture()(lambda run: setattr(run, "ok", False))
    assert code == EXIT_VALIDATION


def test_failure_code_is_overridable():
    """A command whose failure is not a validation failure can say so."""

    def body(run):
        run.ok = False
        run.failure_code = EXIT_FAILURE

    code, _ = _capture()(body)
    assert code == EXIT_FAILURE


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UsageError("wrong place"), EXIT_USAGE),
        (ValidationError(Diagnostics()), EXIT_VALIDATION),
        (StrataError("boom"), EXIT_FAILURE),
    ],
)
def test_raised_failures_map_to_their_code(error, expected):
    """Classification comes from the error type, in one place."""

    def body(run):
        raise error

    code, _ = _capture()(body)
    assert code == expected


def test_unexpected_exceptions_are_not_swallowed():
    """A real bug must surface as a traceback, not a tidy exit code."""

    def body(run):
        raise RuntimeError("genuine bug")

    with pytest.raises(RuntimeError, match="genuine bug"):
        _capture()(body)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def test_invocation_and_timestamp_are_automatic():
    """Identical for every command, so no command should have to add them."""
    _, text = _capture()(lambda run: run.step("working"))
    assert "invocation" in text
    assert "started" in text


def test_describe_can_be_called_after_doing_the_work():
    """validate cannot name the solution until it has loaded it."""

    def body(run):
        run.describe(solution="example")
        run.step("discovered 14 documents")

    _, text = _capture()(body)
    assert text.index("example") < text.index("discovered 14 documents")


def test_header_is_written_once():
    """Lazy emission must not repeat on every step."""

    def body(run):
        run.describe(solution="example")
        run.step("one")
        run.step("two")

    _, text = _capture()(body)
    assert text.count("solution") == 1


def test_header_appears_even_without_describe():
    """A command that describes nothing still reports what ran."""
    _, text = _capture()(lambda run: None)
    assert "demo" in text


# ---------------------------------------------------------------------------
# Run correlation
# ---------------------------------------------------------------------------


def test_run_id_is_in_the_json_envelope():
    """The correlation key a support request needs."""
    _, text = _capture(output="json")(lambda run: None)
    assert len(json.loads(text)["context"]["run_id"]) == 32


def test_run_id_is_not_in_the_console_header():
    """Noise for a human reading a run that passed; logs carry it."""
    _, text = _capture()(lambda run: run.describe(solution="example"))
    assert "run_id" not in text


def test_each_run_gets_a_distinct_id():
    """Two runs must be distinguishable in a shared log stream."""
    _, first = _capture(output="json")(lambda run: None)
    _, second = _capture(output="json")(lambda run: None)
    assert json.loads(first)["context"]["run_id"] != json.loads(second)["context"]["run_id"]


# ---------------------------------------------------------------------------
# Failure rendering
# ---------------------------------------------------------------------------


def test_json_failure_is_still_one_parseable_document():
    """Pipelines parse stdout after a non-zero exit."""

    def body(run):
        raise UsageError("not inside a solution")

    code, text = _capture(output="json")(body)
    payload = json.loads(text)
    assert code == EXIT_USAGE
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "UsageError"


def test_console_failure_has_no_report_around_it(capsys):
    """'You are in the wrong directory' needs no header and summary."""

    def body(run):
        raise UsageError("not inside a solution")

    code, text = _capture()(body)
    assert code == EXIT_USAGE
    assert "PASSED" not in text and "FAILED" not in text
    assert "not inside a solution" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_findings_are_reported_with_a_summary():
    """report() hands the summary to the footer without the command relaying it."""

    def body(run):
        diagnostics = Diagnostics()
        diagnostics.error("broken", source="a.yaml")
        run.report(diagnostics, document_count=2)
        run.ok = False

    code, text = _capture()(body)
    assert code == EXIT_VALIDATION
    assert "broken" in text
    assert "2 documents checked" in text
    assert "FAILED" in text


def test_quiet_suppresses_chrome_on_success():
    """Nothing to say when there is nothing wrong."""
    _, text = _capture(quiet=True)(lambda run: run.step("working"))
    assert text.strip() == ""
