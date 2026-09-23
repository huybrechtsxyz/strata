#!/usr/bin/env python3
"""Tests for machine-readable output."""

import io
import json
from pathlib import Path

from strata.commands.json_output import JsonReporter, build_envelope, format_json
from strata.commands.output import ConsoleReporter, Reporter
from strata.utils.diagnostics import Diagnostics


def _findings() -> Diagnostics:
    diagnostics = Diagnostics()
    diagnostics.error("Field required", source="/sln/workspaces/main.yaml", location="spec.provisioners", code="missing")
    diagnostics.warning("pin matched no remote", source="/sln/versions/prd.yaml", code="stale_pin")
    return diagnostics


def _emit(**kwargs) -> dict:
    """Run a reporter end to end and parse what it wrote."""
    stream = io.StringIO()
    reporter = JsonReporter("validate", stream=stream)
    reporter.header(kwargs.get("context", {"solution": "example"}))
    reporter.step("ignored")
    reporter.diagnostics(
        kwargs.get("diagnostics", Diagnostics()),
        root=kwargs.get("root"),
        document_count=kwargs.get("document_count"),
    )
    reporter.data.update(kwargs.get("data", {}))
    reporter.footer(kwargs.get("ok", True))
    return json.loads(stream.getvalue())


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


def test_envelope_reports_the_verdict_and_producer():
    """A stored document must say what produced it, once the exit code is gone."""
    envelope = _emit(ok=False)
    assert envelope["ok"] is False
    assert envelope["command"] == "validate"
    assert envelope["version"]


def test_context_is_passed_through_verbatim():
    """Console and JSON must not describe the same run differently."""
    context = {"solution": "example", "root": "/sln", "invocation": "strata validate"}
    assert _emit(context=context)["context"] == context


def test_summary_keys_are_always_present():
    """A consumer should not test for a field just because a count is zero."""
    summary = _emit()["summary"]
    assert summary["errors"] == 0
    assert summary["warnings"] == 0
    assert summary["info"] == 0


def test_summary_counts_by_severity():
    """Avoids jq gymnastics over the diagnostics array."""
    summary = _emit(diagnostics=_findings(), document_count=14)["summary"]
    assert (summary["errors"], summary["warnings"], summary["documents"]) == (1, 1, 14)


def test_document_count_is_omitted_when_unknown():
    """Not every command examines documents."""
    assert "documents" not in _emit()["summary"]


def test_diagnostics_carry_their_structure():
    """The whole point of Diagnostics: fields, not a flattened string."""
    first = _emit(diagnostics=_findings())["diagnostics"][0]
    assert first["severity"] == "error"
    assert first["location"] == "spec.provisioners"
    assert first["code"] == "missing"
    assert first["message"] == "Field required"


def test_sources_are_relative_to_the_root():
    """Stable between a laptop and CI, and diffable."""
    envelope = _emit(diagnostics=_findings(), root=Path("/sln"))
    assert envelope["diagnostics"][0]["source"] == "workspaces/main.yaml"


def test_data_is_the_payload_slot():
    """`values get` fills this; `validate` leaves it empty."""
    assert _emit(data={"results": {"KEY": "value"}})["data"]["results"]["KEY"] == "value"


def test_data_defaults_to_an_object():
    """Consumers can index it unconditionally."""
    assert _emit()["data"] == {}


def test_no_duplicate_errors_array():
    """One representation of the findings, not two that can drift."""
    assert "errors" not in _emit(diagnostics=_findings())


# ---------------------------------------------------------------------------
# The invariant that breaks pipelines
# ---------------------------------------------------------------------------


def test_exactly_one_document_is_written():
    """Pipelines capture stdout and pipe it straight to jq."""
    stream = io.StringIO()
    reporter = JsonReporter("validate", stream=stream)
    reporter.header({"solution": "example"})
    reporter.step("discovering")
    reporter.step("validating")
    reporter.diagnostics(_findings())
    reporter.footer(ok=False)
    json.loads(stream.getvalue())  # raises if more than one document


def test_failure_still_emits_parseable_json():
    """haven parses stdout *after* a non-zero exit; v1 sometimes could not."""
    envelope = _emit(ok=False, diagnostics=_findings())
    assert envelope["ok"] is False
    assert envelope["diagnostics"]


def test_steps_print_nothing():
    """Progress on stdout would make the document unparseable."""
    stream = io.StringIO()
    reporter = JsonReporter("validate", stream=stream)
    reporter.step("discovering documents")
    assert stream.getvalue() == ""


def test_header_prints_nothing():
    """Chrome is not suppressed-by-styling here; it is simply not written."""
    stream = io.StringIO()
    reporter = JsonReporter("validate", stream=stream)
    reporter.header({"solution": "example"})
    assert stream.getvalue() == ""


# ---------------------------------------------------------------------------
# Framing, so NDJSON can be added later
# ---------------------------------------------------------------------------


def test_envelope_construction_is_separate_from_writing():
    """A future NDJSON writer reuses the content and changes only the framing."""
    envelope = build_envelope(command="validate", ok=True, diagnostics=Diagnostics())
    assert isinstance(envelope, dict)


def test_can_render_on_a_single_line():
    """NDJSON needs one document per line."""
    envelope = build_envelope(command="validate", ok=True, diagnostics=Diagnostics())
    rendered = format_json(envelope, indent=None)
    assert "\n" not in rendered
    assert json.loads(rendered)["command"] == "validate"


def test_non_ascii_is_kept_readable():
    """Messages carry em dashes and quotes; escaping them helps nobody."""
    diagnostics = Diagnostics()
    diagnostics.error("geography 'europe' — not declared")
    rendered = format_json(build_envelope(command="v", ok=False, diagnostics=diagnostics))
    assert "—" in rendered


# ---------------------------------------------------------------------------
# Interchangeability
# ---------------------------------------------------------------------------


def test_both_reporters_satisfy_the_protocol():
    """A command picks a renderer once, then reports the same way."""

    def use(reporter: Reporter) -> None:
        reporter.header({"solution": "example"})
        reporter.step("working")
        summary = reporter.diagnostics(Diagnostics(), document_count=1)
        reporter.footer(True, summary)

    use(JsonReporter("validate", stream=io.StringIO()))
    use(ConsoleReporter("validate", stream=io.StringIO(), colour=False))
