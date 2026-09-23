#!/usr/bin/env python3
"""Tests for cross-document reference checking."""

from pathlib import Path

import pytest

from strata.controllers.references import references_in, validate_references
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  remotes:
    - name: infra
      type: git
      url: https://example.com/infra.git
      reference: v1.0.0
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - {provider}
  provisioners:
    - name: tf
      tool: terraform
      source:
        remote: {remote}
        source_path: terraform/main
"""


def _solution(tmp_path: Path, *, provider: str = "azure-main", remote: str = "infra") -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    (root / "workspace.yaml").write_text(
        WORKSPACE.format(provider=provider, remote=remote), encoding="utf-8"
    )
    return root


def _check(tmp_path: Path, **kwargs):
    context = open_solution(_solution(tmp_path, **kwargs))
    assert context.diagnostics.ok, context.diagnostics.messages()
    return validate_references(context.controller.index, context.controller.solution)


# ---------------------------------------------------------------------------
# Path walking
# ---------------------------------------------------------------------------


class _Leaf:
    def __init__(self, value):
        self.resource = value


class _Spec:
    def __init__(self):
        self.providers = ["a", "b"]
        self.workspace = "main"
        self.missing = None
        self.resources = [_Leaf("one"), _Leaf("two")]


class _Doc:
    def __init__(self):
        self.spec = _Spec()


def test_walks_a_scalar_field():
    """A single name yields one reference at its own path."""
    assert list(references_in(_Doc(), "spec.workspace")) == [("spec.workspace", "main")]


def test_walks_a_list_of_names():
    """Each entry is located by index, so the error points at the right one."""
    assert list(references_in(_Doc(), "spec.providers")) == [
        ("spec.providers.0", "a"),
        ("spec.providers.1", "b"),
    ]


def test_walks_into_a_list_of_objects():
    """`[]` iterates, then the remaining path applies to each item."""
    assert list(references_in(_Doc(), "spec.resources[].resource")) == [
        ("spec.resources.0.resource", "one"),
        ("spec.resources.1.resource", "two"),
    ]


def test_absent_fields_yield_nothing():
    """Optional references are simply absent, not errors."""
    assert list(references_in(_Doc(), "spec.missing")) == []
    assert list(references_in(_Doc(), "spec.nonexistent")) == []


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolvable_references_produce_nothing(tmp_path):
    """A correct solution stays silent."""
    assert _check(tmp_path).ok


def test_unknown_document_reference_is_an_error(tmp_path):
    """The hole this closes: a name pointing at nothing used to pass."""
    result = _check(tmp_path, provider="does-not-exist")
    assert not result.ok
    assert result.errors[0].code == "unknown_reference"
    assert result.errors[0].location == "spec.providers.0"


def test_unknown_remote_is_an_error(tmp_path):
    """Remotes resolve against the manifest, not the document index."""
    result = _check(tmp_path, remote="nowhere")
    assert not result.ok
    assert result.errors[0].code == "unknown_remote"


def test_error_names_the_document_it_came_from(tmp_path):
    """Provenance replaces the `file:` pointers v1 needed."""
    result = _check(tmp_path, provider="ghost")
    assert "workspace.yaml" in str(result.errors[0].source)


def test_a_near_miss_suggests_a_correction(tmp_path):
    """Typos are the common case; guessing beats listing."""
    result = _check(tmp_path, provider="azure-man")
    assert "did you mean 'azure-main'" in result.errors[0].message


def test_a_wild_miss_lists_what_exists(tmp_path):
    """With no plausible match, show the options."""
    result = _check(tmp_path, provider="zzzzzzzz")
    assert "Available: ['azure-main']" in result.errors[0].message


def test_an_empty_target_kind_says_so(tmp_path):
    """'Available: []' would be a puzzle; say none exist."""
    root = _solution(tmp_path)
    (root / "deployment.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: d\n"
        "spec:\n  workspace: main\n  environments: [nope]\n",
        encoding="utf-8",
    )
    context = open_solution(root)
    result = validate_references(context.controller.index, context.controller.solution)
    assert "no environment documents exist" in result.errors[0].message


def test_remote_checks_are_skipped_without_a_manifest(tmp_path):
    """Reference checking still works when the manifest is unavailable."""
    context = open_solution(_solution(tmp_path, remote="nowhere"))
    assert validate_references(context.controller.index, None).ok


# ---------------------------------------------------------------------------
# Rules come from field metadata, not a hand-written table
# ---------------------------------------------------------------------------


def test_topology_internals_are_not_index_references():
    """`components[].resource`/`.namespace` name workspace-local instances,
    not documents. An instance `web-storage` may be built from a Resource
    document called `storage-account`; checking it here would invent
    failures. `WorkspaceService.validate_topology_references` checks it in
    scope. `components[].modules[].module` IS a document reference (Module)
    and must still be found — this only excludes the instance fields.
    """
    from strata.models.reference_fields import extract_references
    from strata.models.topology_model import TopologyModel

    paths = {rule.path for rule in extract_references(TopologyModel)}
    assert not any(path.endswith(("components[].resource", "components[].namespace")) for path in paths)
    assert any(path.endswith("module") for path in paths)


def test_every_discovered_rule_targets_a_real_kind_or_a_remote():
    """A bug in the walker would produce a garbage target silently."""
    from strata.models.deployment_model import DeploymentModel
    from strata.models.reference_fields import extract_references
    from strata.models.workspace_model import WorkspaceModel

    for model_cls in (WorkspaceModel, DeploymentModel):
        for rule in extract_references(model_cls):
            assert rule.kind is None or isinstance(rule.kind, PlatformKind)


def test_workspace_references_are_discovered_from_its_own_fields():
    """Every reference field workspace.yaml actually has, found by introspection."""
    from strata.models.reference_fields import extract_references
    from strata.models.workspace_model import WorkspaceModel

    paths = {rule.path for rule in extract_references(WorkspaceModel)}
    assert paths == {
        "spec.providers[]",
        "spec.topology[]",
        "spec.namespaces[]",
        "spec.firewalls[]",
        "spec.dns_zones[]",
        "spec.networks[]",
        "spec.resources[].resource",
        "spec.provisioners[].source.remote",
    }


def test_module_reference_is_found_wherever_it_is_embedded():
    """The gap the old table had: neither Topology nor Namespace was a key in it."""
    from strata.models.namespace_model import NamespaceModel
    from strata.models.reference_fields import extract_references
    from strata.models.topology_model import TopologyModel

    assert any(r.path.endswith("module") for r in extract_references(NamespaceModel))
    assert any(r.path.endswith("module") for r in extract_references(TopologyModel))



# ---------------------------------------------------------------------------
# Integration with the context
# ---------------------------------------------------------------------------


def test_resolve_merges_findings_into_the_context(tmp_path):
    """`ok` must account for cross-document failures, not just schema ones."""
    context = open_solution(_solution(tmp_path, provider="ghost"))
    assert context.ok  # schema alone is fine
    context.resolve()
    assert not context.ok


def test_resolve_short_circuits_when_loading_failed(tmp_path):
    """Derived 'unknown reference' noise would bury the real error."""
    root = _solution(tmp_path)
    (root / "broken.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: b\nspec:\n  providers: [x]\n",
        encoding="utf-8",
    )
    context = open_solution(root)
    assert not context.ok
    assert len(context.resolve()) == 0


def test_require_valid_runs_cross_document_checks(tmp_path):
    """build/deploy must not be able to skip them."""
    from strata.utils.errors import ValidationError

    context = open_solution(_solution(tmp_path, provider="ghost"))
    with pytest.raises(ValidationError):
        context.require_valid()
