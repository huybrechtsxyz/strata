#!/usr/bin/env python3
"""Tests for structured diagnostics."""

import dataclasses
import json

import pytest

from strata.utils.diagnostics import Diagnostic, Diagnostics, Severity

# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------


def test_diagnostic_renders_all_parts():
    """Human output reads source, then location, then message."""
    item = Diagnostic(
        severity=Severity.ERROR,
        message="Field required",
        source="config/workspaces/main.yaml",
        location="spec.provisioners",
        code="missing",
    )
    assert str(item) == "config/workspaces/main.yaml: spec.provisioners: Field required [missing]"


def test_diagnostic_omits_absent_parts():
    """A bare finding renders as just its message."""
    assert str(Diagnostic(severity=Severity.ERROR, message="Something broke")) == "Something broke"


def test_diagnostic_is_immutable():
    """A finding records what was observed; nothing may edit it afterwards."""
    item = Diagnostic(severity=Severity.WARNING, message="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.message = "y"  # type: ignore[misc]


def test_diagnostic_to_dict_omits_absent_fields():
    """JSON output stays compact and stable."""
    item = Diagnostic(severity=Severity.WARNING, message="Pin matched nothing")
    assert item.to_dict() == {"severity": "warning", "message": "Pin matched nothing"}


def test_diagnostic_to_dict_is_json_serialisable():
    """The machine-readable path must actually serialise."""
    item = Diagnostic(
        severity=Severity.ERROR,
        message="Field required",
        source="a.yaml",
        location="spec.x",
        code="missing",
    )
    restored = json.loads(json.dumps(item.to_dict()))
    assert restored["severity"] == "error"
    assert restored["location"] == "spec.x"


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------


def test_empty_bag_is_ok():
    """Nothing found means nothing wrong."""
    diagnostics = Diagnostics()
    assert diagnostics.ok
    assert len(diagnostics) == 0


def test_error_makes_the_bag_not_ok():
    """An error is the only thing that fails a run."""
    diagnostics = Diagnostics()
    diagnostics.error("broken")
    assert not diagnostics.ok


@pytest.mark.parametrize("record", ["warning", "info"])
def test_non_errors_leave_the_bag_ok(record):
    """Warnings and info are reportable without failing the run."""
    diagnostics = Diagnostics()
    getattr(diagnostics, record)("noted")
    assert diagnostics.ok
    assert len(diagnostics) == 1


def test_ok_cannot_disagree_with_contents():
    """`ok` is derived, so success-while-holding-an-error is unrepresentable."""
    diagnostics = Diagnostics()
    diagnostics.warning("fine")
    assert diagnostics.ok
    diagnostics.error("not fine")
    assert not diagnostics.ok


def test_findings_keep_discovery_order():
    """Severity does not reorder findings; related ones stay adjacent."""
    diagnostics = Diagnostics()
    diagnostics.warning("first")
    diagnostics.error("second")
    diagnostics.info("third")
    assert [item.message for item in diagnostics] == ["first", "second", "third"]


def test_severity_filters():
    """Each severity is retrievable without scanning by hand."""
    diagnostics = Diagnostics()
    diagnostics.error("e")
    diagnostics.warning("w")
    diagnostics.info("i")
    assert [d.message for d in diagnostics.errors] == ["e"]
    assert [d.message for d in diagnostics.warnings] == ["w"]
    assert [d.message for d in diagnostics.infos] == ["i"]


def test_bag_has_no_bool_conversion():
    """`if diagnostics:` reads as both 'has findings' and 'is ok' — banned.

    dataclasses do not define __bool__, so truthiness must not be inherited
    from __len__ either; callers have to say `.ok` or `len(...)`.
    """
    assert "__bool__" not in Diagnostics.__dict__


# ---------------------------------------------------------------------------
# Bottom-up merging
# ---------------------------------------------------------------------------


def test_extend_merges_findings():
    """A controller absorbs a service's findings."""
    service = Diagnostics()
    service.error("bad field")
    controller = Diagnostics()
    controller.extend(service)
    assert [d.message for d in controller] == ["bad field"]
    assert not controller.ok


def test_extend_attributes_a_source_when_missing():
    """A service reports on a document without knowing the file it came from."""
    service = Diagnostics()
    service.error("bad field", location="spec.x")
    controller = Diagnostics()
    controller.extend(service, source="config/a.yaml")
    assert controller.items[0].source == "config/a.yaml"
    assert controller.items[0].location == "spec.x"


def test_extend_does_not_overwrite_an_existing_source():
    """A finding that already knows where it came from keeps it."""
    inner = Diagnostics()
    inner.error("bad", source="original.yaml")
    outer = Diagnostics()
    outer.extend(inner, source="wrapper.yaml")
    assert outer.items[0].source == "original.yaml"


def test_extend_does_not_mutate_the_source_bag():
    """Merging is non-destructive — the lower layer keeps its own findings."""
    inner = Diagnostics()
    inner.error("bad")
    outer = Diagnostics()
    outer.extend(inner, source="a.yaml")
    assert inner.items[0].source is None
    assert len(inner) == 1


def test_extend_accumulates_across_many_sources():
    """One run reports everything wrong, not just the first failure."""
    controller = Diagnostics()
    for name in ("a.yaml", "b.yaml", "c.yaml"):
        document = Diagnostics()
        document.error("invalid")
        controller.extend(document, source=name)
    assert len(controller.errors) == 3
    assert [d.source for d in controller] == ["a.yaml", "b.yaml", "c.yaml"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_messages_renders_every_finding():
    """The text bridge for callers that only want strings."""
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml", location="spec.x")
    assert diagnostics.messages() == ["a.yaml: spec.x: broken"]


def test_messages_can_filter_by_severity():
    """Errors can be reported separately from warnings."""
    diagnostics = Diagnostics()
    diagnostics.error("e")
    diagnostics.warning("w")
    assert diagnostics.messages(Severity.ERROR) == ["e"]


def test_to_list_is_json_serialisable():
    """`--output json` renders the whole bag without extra conversion."""
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml", location="spec.x", code="missing")
    diagnostics.warning("odd")
    payload = json.loads(json.dumps(diagnostics.to_list()))
    assert payload[0]["code"] == "missing"
    assert payload[1]["severity"] == "warning"
