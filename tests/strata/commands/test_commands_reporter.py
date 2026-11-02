#!/usr/bin/env python3
"""Tests for the console run report: header, steps, footer."""

import io

import pytest

from strata.commands.output import ConsoleReporter, report_width, symbols_for
from strata.utils.diagnostics import Diagnostics


class _Stream(io.StringIO):
    """A stream that reports an encoding, like a real file object."""

    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:
        return self._encoding


def _reporter(**kwargs) -> tuple[ConsoleReporter, _Stream]:
    stream = _Stream()
    return ConsoleReporter("validate", stream=stream, colour=False, **kwargs), stream


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def test_header_names_the_command_and_version():
    """A bug report needs to know which strata ran, and what it ran."""
    reporter, stream = _reporter()
    reporter.header({"solution": "example"})
    output = stream.getvalue()
    assert "validate" in output
    assert "strata " in output


def test_header_renders_context_as_aligned_pairs():
    """Facts about this run, scannable as a block."""
    reporter, stream = _reporter()
    reporter.header({"solution": "example", "root": "/tmp/sln"})
    lines = [line for line in stream.getvalue().splitlines() if "solution" in line or "root" in line]
    assert lines[0].index("example") == lines[1].index("/tmp/sln")


def test_header_always_records_when_it_ran():
    """Timestamps correlate a run with other logs; never omitted."""
    reporter, stream = _reporter()
    reporter.header({"solution": "example"})
    assert "UTC" in stream.getvalue()


def test_caller_can_override_the_timestamp():
    """Supplied context wins, so a caller can pin it for reproducibility."""
    reporter, stream = _reporter()
    reporter.header({"started": "fixed-value"})
    assert "fixed-value" in stream.getvalue()


def test_header_works_without_context():
    """The chrome must not require the caller to know anything."""
    reporter, stream = _reporter()
    reporter.header()
    assert "validate" in stream.getvalue()


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def test_steps_report_progress():
    """A long run should say what it is doing while it does it."""
    reporter, stream = _reporter()
    reporter.step("discovering documents")
    assert "discovering documents" in stream.getvalue()


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def test_footer_states_the_verdict_and_duration():
    """Pass/fail must be unmissable at the end of a long report."""
    reporter, stream = _reporter()
    reporter.footer(ok=True, summary="14 documents checked")
    output = stream.getvalue()
    assert "PASSED" in output
    assert "14 documents checked" in output
    assert "s)" in output


def test_footer_reports_failure():
    """The failing verdict is the one that matters most."""
    reporter, stream = _reporter()
    reporter.footer(ok=False, summary="1 error")
    assert "FAILED" in stream.getvalue()


def test_diagnostics_returns_the_summary_for_the_footer():
    """No hidden state between the two calls."""
    reporter, _ = _reporter()
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml")
    assert reporter.diagnostics(diagnostics, document_count=2) == "2 documents checked — 1 error"


# ---------------------------------------------------------------------------
# Quiet
# ---------------------------------------------------------------------------


def test_quiet_suppresses_chrome_and_progress():
    """Scripted use wants the findings, not the decoration."""
    reporter, stream = _reporter(quiet=True)
    reporter.header({"solution": "example"})
    reporter.step("discovering documents")
    reporter.footer(ok=True, summary="all good")
    assert stream.getvalue() == ""


def test_quiet_still_reports_findings():
    """--quiet must not become a way to hide problems."""
    reporter, stream = _reporter(quiet=True)
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml")
    reporter.diagnostics(diagnostics)
    assert "broken" in stream.getvalue()


def test_quiet_still_reports_failure():
    """A silent failure would be the worst possible outcome."""
    reporter, stream = _reporter(quiet=True)
    reporter.footer(ok=False, summary="1 error")
    assert "FAILED" in stream.getvalue()


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_box_drawing_used_when_the_stream_can_encode_it():
    """A capable terminal gets the nicer rendering."""
    assert symbols_for(_Stream("utf-8"))["rule"] == "\u2500"


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_ascii_fallback_on_legacy_encodings(encoding):
    """Redirecting on a legacy Windows code page must not crash on decoration."""
    assert symbols_for(_Stream(encoding))["rule"] == "-"


def test_report_survives_a_legacy_encoding_end_to_end():
    """The real failure this guards: UnicodeEncodeError from chrome."""
    stream = _Stream("cp1252")
    reporter = ConsoleReporter("validate", stream=stream, colour=False)
    reporter.header({"solution": "example"})
    reporter.step("working")
    reporter.footer(ok=True, summary="done")
    stream.getvalue().encode("cp1252")


def test_width_leaves_a_spare_column():
    """A rule filling the terminal wraps and eats the blank line after it."""
    import shutil

    assert report_width() <= max(60, shutil.get_terminal_size((80, 24)).columns - 1)
