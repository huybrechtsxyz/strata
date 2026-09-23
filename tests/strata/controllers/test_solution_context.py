#!/usr/bin/env python3
"""Tests for opening a solution — the shared precondition."""

import dataclasses
from pathlib import Path

import pytest

from strata.controllers.solution_context import SolutionContext, open_solution
from strata.utils.errors import UsageError, ValidationError

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

TOPOLOGY = """apiVersion: strata.huybrechts.xyz/v2
kind: topology
meta:
  name: main-topology
spec:
  type: kubernetes
  components:
    - resource: web
"""


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "solution"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "topology.yaml").write_text(TOPOLOGY, encoding="utf-8")
    return root


def _broken_solution(tmp_path: Path) -> Path:
    root = _solution(tmp_path)
    (root / "bad.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: topology\nmeta:\n  name: broken\nspec:\n  type: kubernetes\n",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Locating the solution
# ---------------------------------------------------------------------------


def test_opens_a_solution_from_its_root(tmp_path):
    """The ordinary case: run in the solution directory."""
    root = _solution(tmp_path)
    context = open_solution(root)
    assert context.root == root.resolve()
    assert context.ok


def test_opens_a_solution_from_a_subdirectory(tmp_path):
    """Discovery walks upwards, so any nested directory works."""
    root = _solution(tmp_path)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    assert open_solution(nested).root == root.resolve()


def test_missing_solution_is_a_usage_error_not_a_validation_error(tmp_path):
    """'Wrong directory' must not look like 'your config is broken'."""
    with pytest.raises(UsageError) as caught:
        open_solution(tmp_path)
    assert "strata.yaml" in str(caught.value)


def test_nonexistent_path_is_a_usage_error(tmp_path):
    """A mistyped path is an invocation mistake, reported as one."""
    with pytest.raises(UsageError, match="does not exist"):
        open_solution(tmp_path / "nope")


def test_defaults_to_the_current_directory(tmp_path, monkeypatch):
    """Omitting the path means 'the solution I am standing in'."""
    root = _solution(tmp_path)
    monkeypatch.chdir(root)
    assert open_solution().root == root.resolve()


# ---------------------------------------------------------------------------
# Findings are returned, not raised
# ---------------------------------------------------------------------------


def test_invalid_documents_do_not_stop_opening(tmp_path):
    """validate must be able to report findings, so loading does not raise."""
    context = open_solution(_broken_solution(tmp_path))
    assert not context.ok
    assert len(context.diagnostics.errors) >= 1


def test_require_valid_raises_with_the_findings(tmp_path):
    """Callers that act on config refuse a partially-loaded index."""
    context = open_solution(_broken_solution(tmp_path))
    with pytest.raises(ValidationError) as caught:
        context.require_valid()
    assert any("bad.yaml" in m for m in caught.value.diagnostics.messages())


def test_require_valid_returns_the_context_when_clean(tmp_path):
    """Chaining stays readable: open_solution(...).require_valid()."""
    context = open_solution(_solution(tmp_path))
    assert context.require_valid() is context


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_context_exposes_the_loaded_index(tmp_path):
    """Callers work from the index, not by re-reading files."""
    context = open_solution(_solution(tmp_path))
    assert len(context.controller.index) == 1


def test_context_is_immutable(tmp_path):
    """A loaded solution is a fact about one run; nothing rebinds it."""
    context = open_solution(_solution(tmp_path))
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.controller = None  # type: ignore[misc]


def test_returns_a_solution_context(tmp_path):
    """The shared shape every entry point receives."""
    assert isinstance(open_solution(_solution(tmp_path)), SolutionContext)


def test_opening_needs_no_command_layer(tmp_path):
    """A server or MCP front-end must be able to use this without the CLI."""
    import strata.controllers.solution_context as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "strata.commands" not in source
