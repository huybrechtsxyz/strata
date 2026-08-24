#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_diagram.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : DiagramService tests for strata CLI.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.diagram_model import DiagramModel
from strata.services.diagram_service import DiagramService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


def _make_service(sources: list | None, template: str | None) -> DiagramService:
    """Construct a DiagramService from an in-memory document."""
    spec: dict = {}
    if sources is not None:
        spec["sources"] = sources
    if template is not None:
        spec["template"] = template
    else:
        spec["layout"] = {"type": "flowchart"}
    return DiagramService(
        data={
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "diagram",
            "meta": {"name": "test_diagram"},
            "spec": spec,
        }
    )


class TestDiagramService:
    @pytest.fixture
    def get_diagram_service(self):
        return DiagramService(_data("diagrams/diagram-topology.yaml"))

    def test_get_model_class(self, get_diagram_service):
        service = get_diagram_service
        assert service._get_model_class() == DiagramModel

    def test_validate_standard(self, get_diagram_service):
        service = get_diagram_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_diagram_service):
        service = get_diagram_service
        service.validate()
        assert service.get_kind() == "diagram"

    def test_validate_layout_only_file(self):
        """A diagram with no template validates — the template is generated later."""
        service = DiagramService(_data("diagrams/diagram-layout-only.yaml"))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"


class TestDiagramServiceTemplateSyntax:
    def test_invalid_template_syntax_is_rejected(self):
        """An unclosed {% for %} fails validation instead of surfacing at render time."""
        service = DiagramService(_data("diagrams/diagram-invalid-template-syntax.yaml"))
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) == 1
        assert "not a usable template" in errors[0]
        assert "diagram_bad_syntax" in errors[0]

    def test_syntax_error_reports_line_number(self):
        service = _make_service(
            [{"type": "topology"}],
            "flowchart TD\n{% for n in topology.nodes %}\n  {{ n.id }}\n",
        )
        is_valid, errors = service.validate()
        assert not is_valid
        assert "line" in errors[0]

    def test_syntax_check_runs_before_variable_check(self):
        """A broken template yields one syntax error, not a pile of variable errors."""
        service = _make_service(None, "{% for x in nope %}{{ alsonope }}")
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) == 1
        assert "not a usable template" in errors[0]

    def test_unknown_filter_is_rejected(self):
        """A filter the render environment does not provide fails at validation time."""
        service = _make_service([{"type": "resources"}], "{{ resources.name | no_such_filter }}")
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) == 1
        assert "no_such_filter" in errors[0]

    def test_builtin_filters_are_accepted(self):
        service = _make_service([{"type": "resources"}], "{{ resources.name | upper | trim }}")
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"


class TestDiagramServiceVariableBinding:
    def test_unbound_variable_is_rejected(self):
        """A typo'd source name fails validation rather than rendering an empty diagram."""
        service = DiagramService(_data("diagrams/diagram-unbound-variable.yaml"))
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) == 1
        assert "'topolgy'" in errors[0]
        assert "topology" in errors[0]

    def test_source_type_binds_its_own_name_by_default(self):
        service = _make_service([{"type": "topology"}], "{{ topology.nodes }}")
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_as_alias_binds_instead_of_type(self):
        """Once a source declares 'as', the raw type name is no longer bound."""
        service = _make_service([{"type": "topology", "as": "topo"}], "{{ topo.nodes }}")
        assert service.validate()[0]

        service = _make_service([{"type": "topology", "as": "topo"}], "{{ topology.nodes }}")
        is_valid, errors = service.validate()
        assert not is_valid
        assert "'topology'" in errors[0]
        assert "['topo']" in errors[0]

    def test_loop_variables_are_not_reported(self):
        """Jinja resolves loop targets itself — they must not count as unbound."""
        service = _make_service(
            [{"type": "topology"}],
            "{% for node in topology.nodes %}{{ node.id }}{% endfor %}",
        )
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_set_assignments_are_not_reported(self):
        service = _make_service(
            [{"type": "drift"}],
            "{% set total = drift.count %}{{ total }}",
        )
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_static_template_with_no_sources_and_no_variables(self):
        service = _make_service(None, "flowchart TD\n  a --> b\n")
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_variable_with_no_sources_reports_empty_binding_list(self):
        service = _make_service(None, "{{ topology.nodes }}")
        is_valid, errors = service.validate()
        assert not is_valid
        assert "spec.sources is empty" in errors[0]

    def test_multiple_unbound_variables_are_all_reported(self):
        service = _make_service([{"type": "topology"}], "{{ alpha }}{{ beta }}")
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) == 2
        assert "'alpha'" in errors[0]
        assert "'beta'" in errors[1]


class TestDiagramServiceDynamicValidation:
    """ADR-0034 Phase 2: hand-authored 'strata://' links in spec.template must resolve."""

    def test_generated_diagram_has_nothing_to_check(self):
        """No spec.template (layout/style sugar generates one at render time) — a
        generated diagram's strata:// URIs are always freshly computed, never stale."""
        service = _make_service([{"type": "topology"}], "{{ topology.nodes }}")
        service.validate()
        assert service._validate_dynamic(work_path="/irrelevant") == (True, [])

    def test_no_work_path_is_a_noop(self):
        """Resolving a URI needs to read other workspace files — skipped without
        work_path, matching every other cross-reference check in strata."""
        service = _make_service(None, 'flowchart TD\n  a["A"]\n  click a "strata://file/missing.yaml"\n')
        service.validate()
        assert service._validate_dynamic(work_path=None) == (True, [])

    def test_template_with_no_click_directives_is_a_noop(self):
        service = _make_service(None, "flowchart TD\n  a --> b\n")
        service.validate()
        assert service._validate_dynamic(work_path="/irrelevant") == (True, [])

    def test_valid_hand_authored_link_passes(self, tmp_path):
        (tmp_path / "deployment.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\nmeta:\n  name: d\nspec: {}\n",
            encoding="utf-8",
        )
        service = _make_service(None, 'flowchart TD\n  a["A"]\n  click a "strata://file/deployment.yaml"\n')
        service.validate()
        is_valid, errors = service._validate_dynamic(work_path=str(tmp_path))
        assert is_valid, f"Validation failed: {errors}"

    def test_broken_hand_authored_link_is_rejected(self, tmp_path):
        service = _make_service(None, 'flowchart TD\n  a["A"]\n  click a "strata://file/does-not-exist.yaml"\n')
        service.validate()
        is_valid, errors = service._validate_dynamic(work_path=str(tmp_path))
        assert not is_valid
        assert "does-not-exist.yaml" in errors[0]
        assert "test_diagram" in errors[0]

    def test_multiple_broken_links_are_all_reported(self, tmp_path):
        service = _make_service(
            None,
            "flowchart TD\n"
            '  a["A"]\n  click a "strata://file/one.yaml"\n'
            '  b["B"]\n  click b "strata://file/two.yaml"\n',
        )
        service.validate()
        is_valid, errors = service._validate_dynamic(work_path=str(tmp_path))
        assert not is_valid
        assert len(errors) == 2

    def test_duplicate_links_are_checked_once(self, tmp_path, monkeypatch):
        """Two nodes pointing at the same broken URI produce one error, not two."""
        import strata.services.diagram_service as diagram_service_module

        original = diagram_service_module.DiagramResolveController.resolve
        calls: list = []

        def counting_resolve(self, uri):
            calls.append(uri)
            return original(self, uri)

        monkeypatch.setattr(diagram_service_module.DiagramResolveController, "resolve", counting_resolve)

        service = _make_service(
            None,
            "flowchart TD\n"
            '  a["A"]\n  click a "strata://file/missing.yaml"\n'
            '  b["B"]\n  click b "strata://file/missing.yaml"\n',
        )
        service.validate()
        is_valid, errors = service._validate_dynamic(work_path=str(tmp_path))
        assert not is_valid
        assert len(errors) == 1
        assert len(calls) == 1

    def test_full_validate_only_runs_the_check_with_configuration_model(self, tmp_path):
        """Mirrors BaseService.validate()'s real gate: _validate_dynamic only runs
        end-to-end (via validate()) when a configuration_model is supplied — i.e.
        `strata validate --deep` with an active profile, same as every other
        cross-reference check in strata."""
        service = _make_service(None, 'flowchart TD\n  a["A"]\n  click a "strata://file/missing.yaml"\n')
        is_valid, errors = service.validate(configuration_model=None, work_path=str(tmp_path))
        assert is_valid, f"Validation should pass without --deep: {errors}"


def _styled_service(highlight: list) -> DiagramService:
    return DiagramService(
        data={
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "diagram",
            "meta": {"name": "styled"},
            "spec": {
                "sources": [{"type": "topology"}],
                "layout": {"type": "flowchart"},
                "style": {"highlight": highlight},
            },
        }
    )


class TestDiagramServiceStyleValidation:
    def test_valid_highlight_rule_passes(self):
        service = _styled_service([{"condition": "status == disabled", "token": "warn"}])
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_unparseable_condition_is_rejected(self):
        service = _styled_service([{"condition": "status ~ disabled", "token": "warn"}])
        is_valid, errors = service.validate()
        assert not is_valid
        assert "Cannot parse condition" in errors[0]

    def test_unknown_token_is_rejected(self):
        """Authored token names are checked strictly, unlike data-derived ones."""
        service = _styled_service([{"condition": "status == disabled", "token": "chartreuse"}])
        is_valid, errors = service.validate()
        assert not is_valid
        assert "not a design token" in errors[0]
        assert "critical" in errors[0]

    def test_style_errors_are_reported_before_template_errors(self):
        """A bad rule is the real problem — do not bury it under variable warnings."""
        service = DiagramService(
            data={
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "diagram",
                "meta": {"name": "styled"},
                "spec": {
                    "template": "{{ nope }}",
                    "style": {"highlight": [{"condition": "bad", "token": "warn"}]},
                },
            }
        )
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) == 1
        assert "Cannot parse condition" in errors[0]

    def test_style_without_highlight_is_fine(self):
        service = DiagramService(
            data={
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "diagram",
                "meta": {"name": "styled"},
                "spec": {
                    "sources": [{"type": "topology"}],
                    "layout": {"type": "flowchart"},
                    "style": {"color_by": "anything_at_all"},
                },
            }
        )
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
