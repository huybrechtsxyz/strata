#!/usr/bin/env python3
"""Tests for `evaluate_image_tag_policy()` (docs/design/image-tag-policy.md, phase 2)."""

from datetime import date
from pathlib import Path

import pytest

from strata.controllers.image_tag_policy import check_image_tag_policy, evaluate_image_tag_policy
from strata.controllers.policy_controller import check_policies, list_policies
from strata.controllers.policy_results import image_tag_policy_result, read_policy_results
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.integrations.resolved_context import EffectiveImage
from strata.models.image_tag_policy_model import ImageTagPolicyModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Severity

_DIGEST = "sha256:" + "a" * 64


def _image(name="api", tag="1.2.3", *, origin="artifact", source=None, via=("variable:IMAGE",)) -> EffectiveImage:
    return EffectiveImage(
        origin=origin,
        name=name,
        image_name=f"registry/{name}",
        image_tag=tag,
        image_ref=f"registry/{name}:{tag}" if tag else f"registry/{name}",
        tag_source=source or ("declared" if tag else "none"),
        via=via,
    )


def _policy(**fields) -> ImageTagPolicyModel:
    return ImageTagPolicyModel.model_validate(fields)


def _codes(diagnostics) -> list[str | None]:
    return [item.code for item in diagnostics]


def test_a_tagged_image_passes_the_empty_policy():
    assert evaluate_image_tag_policy([_image()], _policy(), None).items == []


@pytest.mark.parametrize("tag", [None, ""])
def test_an_image_without_a_tag_fails_require_tag(tag):
    diagnostics = evaluate_image_tag_policy([_image(tag=tag)], _policy(), None)
    assert _codes(diagnostics) == ["image_without_tag"]
    assert diagnostics.errors[0].message.startswith(
        "image_tag_policy: artifact 'api' (registry/api; used by variable:IMAGE)"
    )


def test_the_fix_names_the_pin_to_add_for_each_origin():
    artifact = evaluate_image_tag_policy([_image(tag=None)], _policy(), None).errors[0].message
    service = (
        evaluate_image_tag_policy([_image("redis", None, origin="module-service")], _policy(), None).errors[0].message
    )
    assert "spec.pins.artifacts.api" in artifact
    assert "spec.pins.images.redis" in service


def test_require_tag_false_allows_an_image_without_a_tag():
    assert evaluate_image_tag_policy([_image(tag=None)], _policy(require_tag=False), None).items == []


@pytest.mark.parametrize("tag", ["latest", "LATEST", "main", "canary"])
def test_the_mutable_aliases_are_rejected_by_default(tag):
    assert _codes(evaluate_image_tag_policy([_image(tag=tag)], _policy(), None)) == ["image_floating_tag"]


def test_a_build_number_is_not_floating():
    assert evaluate_image_tag_policy([_image(tag="env_sbx_20260907.11")], _policy(), None).items == []


def test_reject_tags_replaces_the_default_list():
    policy = _policy(reject_tags=["Stable"])
    assert _codes(evaluate_image_tag_policy([_image(tag="stable")], policy, None)) == ["image_floating_tag"]
    assert evaluate_image_tag_policy([_image(tag="latest")], policy, None).items == []


def test_an_empty_reject_tags_rejects_nothing():
    assert evaluate_image_tag_policy([_image(tag="latest")], _policy(reject_tags=[]), None).items == []


def test_a_digest_passes_the_tag_rules_but_still_needs_a_tag_source():
    policy = _policy(tag_pattern=r"^\d+\.\d+\.\d+$", reject_tags=[_DIGEST])
    assert evaluate_image_tag_policy([_image(tag=_DIGEST)], policy, None).items == []


def test_tag_pattern_is_searched_in_the_tag():
    policy = _policy(tag_pattern=r"^\d+\.\d+\.\d+$")
    assert evaluate_image_tag_policy([_image(tag="1.2.3")], policy, None).items == []
    diagnostics = evaluate_image_tag_policy([_image(tag="1.2")], policy, None)
    assert _codes(diagnostics) == ["image_tag_pattern"]
    assert "'1.2'" in diagnostics.errors[0].message
    assert evaluate_image_tag_policy([_image(tag="release-7")], _policy(tag_pattern="release"), None).items == []


def test_a_floating_tag_that_also_misses_the_pattern_gets_two_findings():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(tag_pattern=r"^\d"), None)
    assert _codes(diagnostics) == ["image_floating_tag", "image_tag_pattern"]


@pytest.mark.parametrize(("source", "failed"), [("pin", False), ("declared", True)])
def test_require_pin_needs_the_tag_to_come_from_a_pin(source, failed):
    diagnostics = evaluate_image_tag_policy([_image(source=source)], _policy(require_pin=True), None)
    assert _codes(diagnostics) == (["image_not_pinned"] if failed else [])


def test_an_untagged_image_is_reported_once_when_both_require_tag_and_require_pin_are_set():
    diagnostics = evaluate_image_tag_policy([_image(tag=None)], _policy(require_pin=True), None)
    assert _codes(diagnostics) == ["image_without_tag"]


def test_require_pin_alone_reports_an_untagged_image_as_not_pinned():
    diagnostics = evaluate_image_tag_policy([_image(tag=None)], _policy(require_tag=False, require_pin=True), None)
    assert _codes(diagnostics) == ["image_not_pinned"]


def test_a_digest_still_has_to_satisfy_require_pin():
    diagnostics = evaluate_image_tag_policy([_image(tag=_DIGEST)], _policy(require_pin=True), None)
    assert _codes(diagnostics) == ["image_not_pinned"]


def test_origins_narrows_which_images_are_judged():
    images = [_image("api", None), _image("redis", None, origin="module-service")]
    only_artifacts = evaluate_image_tag_policy(images, _policy(origins=["artifact"]), None)
    assert [d.message.split("'")[1] for d in only_artifacts] == ["api"]
    assert len(evaluate_image_tag_policy(images, _policy(), None)) == 2


def test_enforcement_decides_the_severity():
    images = [_image(tag="latest")]
    deny = evaluate_image_tag_policy(images, _policy(enforcement="deny"), None)
    warn = evaluate_image_tag_policy(images, _policy(enforcement="warn"), None)
    assert (deny.ok, [d.severity for d in warn], warn.ok) == (False, [Severity.WARNING], True)


def test_findings_are_reported_per_image_in_order():
    diagnostics = evaluate_image_tag_policy(
        [_image("a", "latest"), _image("b", None), _image("c", "1.0.0")], _policy(), None
    )
    assert [d.message.split("'")[1] for d in diagnostics] == ["a", "b"]


_ALLOW = {"name": "redis", "reason": "third party"}


def test_an_allowed_image_is_exempt_from_every_rule():
    policy = _policy(allow=[_ALLOW], require_pin=True, tag_pattern="x")
    assert evaluate_image_tag_policy([_image("redis", "latest", origin="module-service")], policy, None).items == []


def test_an_allow_entry_exempts_only_the_name_it_lists():
    policy = _policy(allow=[_ALLOW])
    assert _codes(evaluate_image_tag_policy([_image("api", "latest")], policy, None)) == ["image_floating_tag"]


def test_an_allow_entry_is_honoured_through_its_expiry_day_and_not_after():
    policy = _policy(allow=[{**_ALLOW, "expires": "2026-12-31"}])
    image = [_image("redis", "latest")]
    assert evaluate_image_tag_policy(image, policy, None, today=date(2026, 12, 31)).items == []
    diagnostics = evaluate_image_tag_policy(image, policy, None, today=date(2027, 1, 1))
    assert _codes(diagnostics) == ["image_floating_tag"]
    assert "expired on 2026-12-31" in diagnostics.errors[0].message


def test_a_ring_outside_the_list_is_not_evaluated_and_says_so():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(rings=["acc", "prd"]), "dev")
    assert (diagnostics.ok, _codes(diagnostics)) == (True, ["image_tag_policy_not_applied"])
    assert (
        diagnostics.infos[0].message
        == "image_tag_policy: not applied, the deployment's ring 'dev' is not one of acc, prd."
    )


def test_a_ring_in_the_list_is_evaluated():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(rings=["acc", "prd"]), "prd")
    assert _codes(diagnostics) == ["image_floating_tag"]


def test_a_deployment_with_no_ring_is_evaluated_even_when_rings_is_set():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(rings=["prd"]), None)
    assert _codes(diagnostics) == ["image_floating_tag"]


def test_no_rings_means_every_ring_is_evaluated():
    assert _codes(evaluate_image_tag_policy([_image(tag="latest")], _policy(), "dev")) == ["image_floating_tag"]


def test_no_images_is_a_pass():
    assert evaluate_image_tag_policy([], _policy(), None).items == []


# ---------------------------------------------------------------------------
# image_tag_policy_result()
# ---------------------------------------------------------------------------


def _workspace(**policy) -> WorkspaceModel:
    spec = {
        "providers": ["azure-main"],
        "provisioners": [{"name": "tf", "tool": "terraform", "source": {"source_path": "terraform/main"}}],
    }
    if policy is not None:
        spec["image_tag_policy"] = policy
    return WorkspaceModel.model_validate({"meta": {"name": "ws"}, "spec": spec})


def test_the_result_is_none_when_the_policy_is_unset():
    workspace = _workspace()
    workspace.spec.image_tag_policy = None
    assert image_tag_policy_result(workspace, evaluate_image_tag_policy([], _policy(), None)) is None


def test_the_result_records_a_failed_deny_policy_with_its_findings():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(), None)
    result = image_tag_policy_result(_workspace(), diagnostics)
    assert result is not None
    assert (result.policy_name, result.policy_type, result.phase, result.enforcement, result.passed) == (
        "image_tag_policy",
        "image_tag",
        "build",
        "deny",
        False,
    )
    assert len(result.violations) == 1


def test_a_warn_breach_passes_but_keeps_its_violation():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(enforcement="warn"), None)
    result = image_tag_policy_result(_workspace(enforcement="warn"), diagnostics, "deploy")
    assert result is not None
    assert (result.phase, result.passed, len(result.violations)) == ("deploy", True, 1)


def test_the_result_of_a_ring_that_is_not_applied_passes_with_no_violations():
    diagnostics = evaluate_image_tag_policy([_image(tag="latest")], _policy(rings=["prd"]), "dev")
    result = image_tag_policy_result(_workspace(rings=["prd"]), diagnostics)
    assert result is not None
    assert (result.passed, result.violations) == (True, [])


# ---------------------------------------------------------------------------
# check_image_tag_policy() and `strata policy check` over a real solution
# ---------------------------------------------------------------------------

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(
    tmp_path: Path, *, policy: str = "  image_tag_policy: {}\n", artifact_tag: str = "", pins: str = "", ring: str = ""
) -> Path:
    """One workspace, one artifact `datahub` reached by a `store: artifact` variable, optional version document."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n" + policy,
    )
    _write(root, "artifact.yaml", "artifact", "datahub", f"  image_name: registry/datahub\n{artifact_tag}")
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: DATAHUB_TAG\n      store: artifact\n      value: datahub\n      field: image_tag\n",
    )
    if pins or ring:
        promotion = f"  promotion:\n    ring: {ring}\n    order: 1\n" if ring else ""
        _write(root, "version.yaml", "version", "prd", f"{promotion}  pins:\n{pins or '    artifacts: {}'}\n")
    version = "  version: prd\n" if pins or ring else ""
    _write(root, "deployment.yaml", "deployment", "app", f"  workspace: main\n{version}  environments:\n    - prd\n")
    return root


def _check(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    deployment, workspace = resolve_deployment_workspace(context, "app")
    return check_image_tag_policy(context, deployment, workspace)


def test_an_untagged_unpinned_artifact_fails_the_policy_for_a_real_solution(tmp_path):
    diagnostics = _check(_solution(tmp_path))
    assert _codes(diagnostics) == ["image_without_tag"]
    assert "variable:DATAHUB_TAG" in diagnostics.errors[0].message


def test_a_version_pin_satisfies_it(tmp_path):
    pins = "    artifacts:\n      datahub:\n        version: '1.4.0'\n"
    assert _check(_solution(tmp_path, pins=pins)).items == []


def test_a_declared_tag_satisfies_it_unless_a_pin_is_required(tmp_path):
    root = _solution(tmp_path, artifact_tag="  image_tag: '1.0.0'\n")
    assert _check(root).items == []


def test_an_unset_policy_reports_nothing(tmp_path):
    assert _check(_solution(tmp_path, policy="")).items == []


def test_the_ring_comes_from_the_deployments_version_document(tmp_path):
    root = _solution(tmp_path, policy="  image_tag_policy:\n    rings: [prd]\n", ring="dev")
    assert _codes(_check(root)) == ["image_tag_policy_not_applied"]


def test_policy_check_evaluates_and_records_the_result(tmp_path):
    root = _solution(tmp_path)
    build_path = tmp_path / "build"
    build_path.mkdir()
    diagnostics, checked = check_policies(open_solution(root), "app", build_path)
    assert checked == ["image_tag_policy"]
    assert _codes(diagnostics) == ["image_without_tag"]
    [recorded] = read_policy_results(build_path)
    assert (recorded.policy_name, recorded.policy_type, recorded.phase, recorded.passed) == (
        "image_tag_policy",
        "image_tag",
        "build",
        False,
    )


def test_policy_check_honours_a_pin_flag(tmp_path):
    root = _solution(tmp_path, pins="    artifacts:\n      datahub:\n        version: '1.4.0'\n")
    context = open_solution(root)
    build_path = tmp_path / "build"
    build_path.mkdir()
    diagnostics, _ = check_policies(context, "app", build_path, version_pin="prd")
    assert diagnostics.ok


def test_policy_list_shows_the_policy_as_checkable(tmp_path):
    [info] = list_policies(open_solution(_solution(tmp_path)), "app", tmp_path / "build")
    assert (info.name, info.phase, info.enforcement, info.checkable, info.findings) == (
        "image_tag_policy",
        "build",
        "deny",
        True,
        False,
    )
