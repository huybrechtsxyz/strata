#!/usr/bin/env python3
"""Parameter sets in deployment chains (`base-parameters-v2.md` (work doc), step 1b).

`meta.params` names parameter sets per role on any document of an `extends` chain; the resolver merges them per
role, root first, and substitutes `${param:}` in every document's spec before the specs merge.
"""

from pathlib import Path

from strata.controllers.deployment_resolution import resolve_deployment_chains, resolve_deployments
from strata.controllers.references import validate_references
from strata.controllers.solution_context import open_solution
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import AnyDeploymentModel, DeploymentModel, DeploymentTemplateModel
from strata.models.environment_model import EnvironmentModel
from strata.models.parameterset_model import ParameterSetModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.environment_service import unresolved_value_tokens
from strata.utils.param_tokens import holds_param_tokens
from strata.utils.value_tokens import find_malformed_value_tokens


def _deployment(name: str, spec: dict, params: dict | None = None) -> AnyDeploymentModel:
    """As the loader would hold it: a template when the spec holds `${param:}`."""
    meta: dict = {"name": name}
    if params is not None:
        meta["params"] = params
    model_class = DeploymentTemplateModel if holds_param_tokens(spec) else DeploymentModel
    return model_class.model_validate({"meta": meta, "spec": spec})


def _set(name: str, values: dict) -> ParameterSetModel:
    return ParameterSetModel.model_validate({"meta": {"name": name}, "spec": {"values": values}})


def _index(*models: AnyDeploymentModel | ParameterSetModel) -> DocumentIndex:
    """The documents, plus the `main` workspace and `prd` environment the bases name."""
    index = DocumentIndex()
    workspace = WorkspaceModel.model_validate(
        {
            "meta": {"name": "main"},
            "spec": {
                "providers": ["p"],
                "provisioners": [{"name": "tf", "tool": "terraform", "source": {"source_path": "infra"}}],
            },
        }
    )
    environment = EnvironmentModel.model_validate({"meta": {"name": "prd"}, "spec": {}})
    for kind, model in ((PlatformKind.WORKSPACE, workspace), (PlatformKind.ENVIRONMENT, environment)):
        index.add(IndexEntry(ref=DocumentRef(kind=kind, name=model.meta.name), model=model, source=Path("x.yaml")))
    for model in models:
        kind = PlatformKind.PARAMETERSET if isinstance(model, ParameterSetModel) else PlatformKind.DEPLOYMENT
        ref = DocumentRef(kind=kind, name=model.meta.name)
        index.add(IndexEntry(ref=ref, model=model, source=Path(f"{model.meta.name}.yaml")))
    return index


LEAF_SPEC = {"extends": "base"}


def _base(properties: dict, params: dict | None = None) -> AnyDeploymentModel:
    spec = {"partial": True, "workspace": "main", "environments": ["prd"], "properties": properties}
    return _deployment("base", spec, params)


# ---------------------------------------------------------------------------
# Merge order (rule 3)
# ---------------------------------------------------------------------------


def test_sets_merge_per_role_root_first_then_down_the_chain_then_the_leaf():
    index = _index(
        _set("root-hub", {"provider": "root", "region": "westeurope", "tags": {"a": "root", "b": "root"}}),
        _set("mid-hub", {"provider": "mid", "tags": {"b": "mid"}}),
        _set("leaf-hub-1", {"provider": "leaf-1", "dns": ["x"]}),
        _set("leaf-hub-2", {"dns": ["y", "z"]}),
        _deployment(
            "root",
            {"partial": True, "workspace": "main", "environments": ["prd"], "properties": {"hub": "${param:hub}"}},
            {"hub": "root-hub"},
        ),
        _deployment("mid", {"partial": True, "extends": "root"}, {"hub": "mid-hub"}),
        _deployment("leaf", {"extends": "mid"}, {"hub": ["leaf-hub-1", "leaf-hub-2"]}),
    )
    resolved, diagnostics = resolve_deployments(index)
    assert diagnostics.ok, diagnostics
    leaf = resolved["leaf"]
    assert leaf.model.spec.properties == {
        "hub": {"provider": "leaf-1", "region": "westeurope", "tags": {"a": "root", "b": "mid"}, "dns": ["y", "z"]}
    }
    assert leaf.param_sets == {"hub": ("root-hub", "mid-hub", "leaf-hub-1", "leaf-hub-2")}
    assert leaf.params == {"hub": leaf.model.spec.properties["hub"]}
    assert leaf.chain == ("leaf", "mid", "root")


def test_two_roles_with_the_same_key_stay_apart_and_one_set_can_serve_two_roles():
    index = _index(
        _set("spoke-acme", {"code": "acme"}),
        _set("customer-acmep01", {"code": "acmep01"}),
        _base({"sub": "st${param:spoke.code}", "project": "${param:customer.code}", "again": "${param:also.code}"}),
        _deployment("leaf", LEAF_SPEC, {"spoke": "spoke-acme", "customer": "customer-acmep01", "also": "spoke-acme"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok, diagnostics
    assert resolved["leaf"].spec.properties == {"sub": "stacme", "project": "acmep01", "again": "acme"}


# ---------------------------------------------------------------------------
# Substitution (rules 4, 6, 8)
# ---------------------------------------------------------------------------


def test_the_leafs_own_spec_reads_the_values_too():
    index = _index(
        _set("spoke-acme", {"name": "acme-001"}),
        _base({"base": "b-${param:spoke.name}"}),
        _deployment("leaf", {"extends": "base", "custom": {"own": "l-${param:spoke.name}"}}, {"spoke": "spoke-acme"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok, diagnostics
    assert resolved["leaf"].spec.properties == {"base": "b-acme-001"}
    assert resolved["leaf"].spec.custom == {"own": "l-acme-001"}


def test_a_list_is_inserted_whole_and_run_time_tokens_are_left_for_run_time():
    index = _index(
        _set("spoke-acme", {"customers": ["acmep01", "acmep02"], "name": "acme-001"}),
        _base({"customers": "${param:spoke.customers}", "db": "${var:db_host}/${param:spoke.name}"}),
        _deployment("leaf", LEAF_SPEC, {"spoke": "spoke-acme"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok, diagnostics
    assert resolved["leaf"].spec.properties == {"customers": ["acmep01", "acmep02"], "db": "${var:db_host}/acme-001"}


def test_one_base_shared_by_two_leaves_reads_each_leafs_values():
    index = _index(
        _set("spoke-acme", {"name": "acme-001"}),
        _set("spoke-initech", {"name": "initech-001"}),
        _base({"name": "${param:spoke.name}"}),
        _deployment("acme", LEAF_SPEC, {"spoke": "spoke-acme"}),
        _deployment("initech", LEAF_SPEC, {"spoke": "spoke-initech"}),
        _deployment("acme-again", LEAF_SPEC, {"spoke": "spoke-acme"}),
    )
    resolved, diagnostics = resolve_deployment_chains(index)
    assert diagnostics.ok, diagnostics
    assert {name: model.spec.properties for name, model in resolved.items()} == {
        "acme": {"name": "acme-001"},
        "initech": {"name": "initech-001"},
        "acme-again": {"name": "acme-001"},
    }


def test_a_chain_without_params_resolves_as_before():
    index = _index(
        _base({"region": "westeurope", "literal": "$${param:not.a.token}"}),
        _deployment("leaf", LEAF_SPEC),
    )
    resolved, diagnostics = resolve_deployments(index)
    assert diagnostics.ok, diagnostics
    assert resolved["leaf"].model.spec.properties == {"region": "westeurope", "literal": "$${param:not.a.token}"}
    assert resolved["leaf"].params == {}
    assert resolved["leaf"].param_sets == {}


def test_an_unused_partial_base_with_tokens_is_not_a_finding():
    _, diagnostics = resolve_deployments(_index(_base({"name": "${param:spoke.name}"})))
    assert diagnostics.ok, diagnostics


# ---------------------------------------------------------------------------
# Problems (rule 7)
# ---------------------------------------------------------------------------


def test_a_missing_key_names_the_document_field_sets_and_leaf():
    index = _index(
        _set("spoke-acme", {"name": "acme-001"}),
        _base({"envs": ["x", "hub-${param:spoke.hub}-env"]}),
        _deployment("leaf", LEAF_SPEC, {"spoke": "spoke-acme"}),
    )
    resolved, diagnostics = resolve_deployments(index)
    assert "leaf" not in resolved
    (finding,) = diagnostics.errors
    assert finding.code == "param_missing"
    assert finding.source == "base.yaml"  # the document that holds the token
    assert finding.location == "spec.properties.envs[1]"
    assert finding.message == (
        "base spec.properties.envs[1]: ${param:spoke.hub} not found in spoke: [spoke-acme] (for leaf)"
    )


def test_a_role_no_document_names_is_a_missing_key_for_each_leaf():
    index = _index(
        _set("spoke-acme", {"name": "acme-001"}),
        _base({"name": "${param:spoke.name}", "code": "${param:customer.code}"}),
        _deployment("one", LEAF_SPEC, {"spoke": "spoke-acme"}),
        _deployment("two", LEAF_SPEC, {"spoke": "spoke-acme"}),
    )
    resolved, diagnostics = resolve_deployments(index)
    assert resolved == {}
    assert sorted(f.message for f in diagnostics.errors) == [
        f"base spec.properties.code: ${{param:customer.code}}: no document in the chain names a parameter set for "
        f"role 'customer' in meta.params (for {leaf})"
        for leaf in ("one", "two")
    ]
    assert {f.code for f in diagnostics.errors} == {"param_missing"}


def test_an_embedded_list_is_param_not_scalar():
    index = _index(
        _set("spoke-acme", {"customers": ["a"]}),
        _base({"x": "c-${param:spoke.customers}"}),
        _deployment("leaf", LEAF_SPEC, {"spoke": "spoke-acme"}),
    )
    _, diagnostics = resolve_deployments(index)
    assert [f.code for f in diagnostics.errors] == ["param_not_scalar"]


def test_an_unknown_parameter_set_is_one_unknown_reference_not_a_cascade():
    index = _index(
        _set("hub-defaults", {"provider": "p"}),
        _base({"name": "${param:spoke.name}"}),
        _deployment("leaf", LEAF_SPEC, {"spoke": "spoke-acmee", "hub": ["hub-defaults", "hub-ghost"]}),
    )
    resolved, diagnostics = resolve_deployments(index)
    assert resolved == {}
    assert diagnostics.ok  # the resolver leaves it to the reference check
    references = validate_references(index)  # this bare index also lacks the workspace and environment
    assert sorted((f.location, f.code) for f in references.errors if f.location.startswith("meta.")) == [
        ("meta.params.hub.1", "unknown_reference"),
        ("meta.params.spoke", "unknown_reference"),
    ]


def test_params_must_name_parameter_sets_by_valid_names():
    for params in ({"Spoke": "spoke-acme"}, {"spoke": "${param:x}"}, {"spoke": 3}):
        try:
            _deployment("leaf", LEAF_SPEC, params)
        except ValueError:
            continue
        raise AssertionError(f"{params} was accepted")


# ---------------------------------------------------------------------------
# Token syntax: `${param:}` is well formed only in a deployment
# ---------------------------------------------------------------------------


def test_param_tokens_are_well_formed_only_when_allowed():
    text = "${param:spoke.name}-${param:}-${params:x}"
    assert find_malformed_value_tokens(text) == ["${param:spoke.name}", "${param:}", "${params:x}"]
    assert find_malformed_value_tokens(text, param_tokens=True) == ["${param:}", "${params:x}"]


def test_a_deployment_with_param_tokens_is_a_template():
    assert isinstance(_base({"name": "${param:spoke.name}"}), DeploymentTemplateModel)
    assert isinstance(_base({"name": "$${param:spoke.name}"}), DeploymentModel)  # an escape is not a token


def test_a_param_token_in_an_environment_is_still_malformed():
    environment = EnvironmentModel.model_validate(
        {"meta": {"name": "prd"}, "spec": {"properties": {"name": "${param:spoke.name}"}}}
    )
    assert [d.code for d in unresolved_value_tokens(environment, {}, "prd").errors] == ["malformed_value_token"]


# ---------------------------------------------------------------------------
# End to end, from files
# ---------------------------------------------------------------------------

_FILES = {
    "strata.yaml": "kind: solution\nmeta: { name: s }\nspec: { configuration: c }\n",
    "config.yaml": "kind: configuration\nmeta: { name: c }\nspec: { providers: [azure] }\n",
    "spoke.yaml": "kind: parameterset\nmeta: { name: spoke-acme-001 }\nspec:\n  values: { name: acme-001 }\n",
    "base.yaml": (
        "kind: deployment\nmeta: { name: base }\n"
        "spec:\n  partial: true\n  workspace: main\n  environments: [prd]\n"
        "  properties: { spoke: '${param:spoke.name}', hub: '${param:spoke.hub}' }\n"
    ),
    "leaf.yaml": "kind: deployment\nmeta: { name: leaf, params: { spoke: spoke-acme-001 } }\nspec: { extends: base }\n",
}


def test_a_missing_key_is_reported_by_validate_against_the_base_file(tmp_path):
    for name, text in _FILES.items():
        (tmp_path / name).write_text("apiVersion: strata.huybrechts.xyz/v2\n" + text, encoding="utf-8")
    context = open_solution(tmp_path)
    assert context.ok, context.diagnostics
    found = context.resolve()
    (finding,) = [f for f in found.errors if f.code.startswith("param_")]
    assert finding.code == "param_missing"
    assert finding.source is not None and finding.source.endswith("base.yaml")
    assert finding.message.endswith("${param:spoke.hub} not found in spoke: [spoke-acme-001] (for leaf)")
    assert not [f for f in found.errors if f.code == "malformed_value_token"]
