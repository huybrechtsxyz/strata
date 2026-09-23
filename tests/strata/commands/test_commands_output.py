#!/usr/bin/env python3
"""Tests for console rendering of diagnostics."""

import pytest

from strata.commands.output import (
    colour_enabled,
    format_console,
    format_diagnostic,
    format_summary,
    group_by_source,
)
from strata.utils.diagnostics import Diagnostic, Diagnostics, Severity


def _plain(diagnostics: Diagnostics, **kwargs) -> str:
    """Render without styling, so assertions compare text not escape codes."""
    return format_console(diagnostics, colour=False, **kwargs)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_findings_group_under_their_document():
    """The author's question is 'what is wrong with this file'."""
    diagnostics = Diagnostics()
    diagnostics.error("first", source="a.yaml")
    diagnostics.error("second", source="b.yaml")
    diagnostics.warning("third", source="a.yaml")

    grouped = group_by_source(diagnostics)
    assert list(grouped) == ["a.yaml", "b.yaml"]
    assert [d.message for d in grouped["a.yaml"]] == ["first", "third"]


def test_grouping_keeps_discovery_order():
    """Documents appear in the order they were loaded, not alphabetically."""
    diagnostics = Diagnostics()
    diagnostics.error("x", source="z.yaml")
    diagnostics.error("y", source="a.yaml")
    assert list(group_by_source(diagnostics)) == ["z.yaml", "a.yaml"]


def test_sourceless_findings_group_separately():
    """Run-level findings are not attributed to a document."""
    diagnostics = Diagnostics()
    diagnostics.error("no source here")
    assert list(group_by_source(diagnostics)) == [""]


# ---------------------------------------------------------------------------
# A single line
# ---------------------------------------------------------------------------


def test_finding_line_omits_the_source():
    """The header already carries it; repeating triples the width."""
    item = Diagnostic(
        severity=Severity.ERROR,
        message="Field required",
        source="config/a.yaml",
        location="spec.provisioners",
        code="missing",
    )
    rendered = format_diagnostic(item, colour=False)
    assert "config/a.yaml" not in rendered
    assert rendered.strip() == "error    spec.provisioners: Field required [missing]"


def test_finding_line_omits_absent_parts():
    """A bare finding is just its severity and message."""
    item = Diagnostic(severity=Severity.WARNING, message="something odd")
    assert format_diagnostic(item, colour=False).strip() == "warning  something odd"


def test_severity_labels_are_aligned():
    """Messages line up so a long report stays scannable."""
    error = format_diagnostic(Diagnostic(severity=Severity.ERROR, message="m"), colour=False)
    warning = format_diagnostic(Diagnostic(severity=Severity.WARNING, message="m"), colour=False)
    assert error.index("m", 2) == warning.index("m", 2)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_reports_a_clean_run():
    """A clean run still confirms that something was actually checked."""
    assert format_summary(Diagnostics(), document_count=14) == "14 documents checked — no problems found"


def test_summary_tallies_each_severity():
    """Counts are reported per severity, in severity order."""
    diagnostics = Diagnostics()
    diagnostics.error("a")
    diagnostics.error("b")
    diagnostics.warning("c")
    assert format_summary(diagnostics, document_count=3) == "3 documents checked — 2 errors, 1 warning"


def test_summary_singularises():
    """'1 error' not '1 errors'."""
    diagnostics = Diagnostics()
    diagnostics.error("a")
    assert format_summary(diagnostics, document_count=1) == "1 document checked — 1 error"


def test_summary_omits_severities_with_no_findings():
    """Zero counts are noise."""
    diagnostics = Diagnostics()
    diagnostics.warning("a")
    assert "error" not in format_summary(diagnostics, document_count=1)


def test_summary_without_a_document_count():
    """Not every caller knows how many documents there were."""
    assert format_summary(Diagnostics()) == "Checked — no problems found"


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------


def test_clean_run_still_prints_a_summary():
    """Silence would be ambiguous: did it pass, or did it not run?"""
    assert _plain(Diagnostics(), document_count=14) == "14 documents checked — no problems found"


def test_report_groups_then_summarises(tmp_path):
    """The whole shape, end to end."""
    diagnostics = Diagnostics()
    diagnostics.error(
        "Field required",
        source=str(tmp_path / "workspaces" / "main.yaml"),
        location="spec.provisioners",
        code="missing",
    )
    diagnostics.warning(
        "pin 'infra' matched no remote",
        source=str(tmp_path / "versions" / "prd.yaml"),
        location="spec.pins.remotes",
        code="stale_pin",
    )

    assert _plain(diagnostics, root=tmp_path, document_count=14).splitlines() == [
        "workspaces/main.yaml",
        "  error    spec.provisioners: Field required [missing]",
        "versions/prd.yaml",
        "  warning  spec.pins.remotes: pin 'infra' matched no remote [stale_pin]",
        "",
        "14 documents checked — 1 error, 1 warning",
    ]


def test_report_has_no_trailing_newline():
    """The caller's echo adds it; two would leave a blank line."""
    assert not _plain(Diagnostics(), document_count=1).endswith("\n")


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def test_colour_can_be_forced_off():
    """Assertions and diffs need plain text."""
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml")
    assert "\x1b[" not in format_console(diagnostics, colour=False)


def test_colour_can_be_forced_on():
    """Severity colour is the most useful affordance in a long report."""
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml")
    assert "\x1b[" in format_console(diagnostics, colour=True)


@pytest.mark.parametrize("value", ["1", "", "anything"])
def test_no_color_env_var_disables_colour(monkeypatch, value):
    """no-color.org: presence of the variable is what counts, not its value."""
    monkeypatch.setenv("NO_COLOR", value)
    assert colour_enabled() is False


def test_colour_is_enabled_without_the_env_var(monkeypatch):
    """Default is styled; click still strips it when piped."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert colour_enabled() is True
