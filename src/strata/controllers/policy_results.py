#!/usr/bin/env python3
"""Policy evaluation results sidecar (`build_path/policy_results.json`) —
docs/work/audit-trail.md's "DeploymentManifestModel.policy_results
population" design. The one shared file-format contract between
`build run` (writes) and `deploy run`'s audit finalize step (reads) —
matches the existing `resolved.yaml`/`sbom.json` hand-off precedent
between the two commands.

Written by build-phase policies (`cve_policy`, `checkov_policy`) and appended to by deploy-phase
ones (`release_gate_policy`, `image_gate_policy`, docs/design/security-gates.md D9) —
validate-phase policies (`tenant_zone`/`path_convention`) are explicitly out of scope for
this pass, since `strata validate` has no `build_path` concept and no
enforced 1:1 relationship to any one later `deploy run` invocation.
"""

import json
from pathlib import Path

from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics, Severity
from strata.utils.errors import SystemError

#: Public — both `build_controller.py` (writes) and `audit_run.py` (reads)
#: must agree on this exact filename.
POLICY_RESULTS_FILENAME = "policy_results.json"


def cve_policy_result(workspace: WorkspaceModel, diagnostics: Diagnostics) -> ManifestPolicyResultModel | None:
    """The `cve_policy` entry for `policy_results.json`, or `None` when the policy is unset.

    Shared by `build run` and `strata policy check` (security-gates plan Phase 8, P8-1). `passed` is
    `diagnostics.ok`, so a `warn` breach passes; `violations` keeps errors and warnings alike, so a
    warn-mode breach is still recorded. v2's single-field policies have no operator-given name, so
    the field name is the `policy_name`.
    """
    policy = workspace.spec.cve_policy
    if policy is None:
        return None
    return _build_phase_result("cve_policy", "cve_max_severity", policy.enforcement, diagnostics)


def checkov_policy_result(workspace: WorkspaceModel, diagnostics: Diagnostics) -> ManifestPolicyResultModel | None:
    """The `checkov_policy` entry for `policy_results.json`, or `None` when unset (as `cve_policy_result()`)."""
    policy = workspace.spec.checkov_policy
    if policy is None:
        return None
    return _build_phase_result("checkov_policy", "checkov", policy.enforcement, diagnostics)


def _build_phase_result(
    name: str, policy_type: str, enforcement: str, diagnostics: Diagnostics
) -> ManifestPolicyResultModel:
    return ManifestPolicyResultModel(
        policy_name=name,
        policy_type=policy_type,
        phase="build",
        enforcement=enforcement,
        passed=diagnostics.ok,
        violations=diagnostics.messages(Severity.ERROR) + diagnostics.messages(Severity.WARNING),
    )


def write_policy_results(build_path: Path, results: list[ManifestPolicyResultModel]) -> None:
    """Write `build_path/policy_results.json`, or do nothing when `results`
    is empty — optional artifact, matching `write_sbom()`'s own "nothing
    relevant, no file" precedent.
    """
    if not results:
        return
    path = build_path / POLICY_RESULTS_FILENAME
    payload = {"policy_results": [r.model_dump(mode="json") for r in results]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_policy_results(build_path: Path) -> list[ManifestPolicyResultModel] | None:
    """Read `build_path/policy_results.json` back, or `None` when it
    doesn't exist — same optional, no-diagnostic shape as
    `audit_run.py`'s own `_sbom_reference()`; a build with no configured
    build-phase policy writes none, and that must not be an error here.
    """
    path = build_path / POLICY_RESULTS_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [ManifestPolicyResultModel.model_validate(item) for item in payload["policy_results"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemError(f"'{path}' is not a readable policy results file ({exc}).") from None


def append_policy_result(build_path: Path, result: ManifestPolicyResultModel) -> None:
    """Merge one result into `build_path/policy_results.json`, creating it when absent.

    Replaces an entry with the same `policy_name` and keeps every other one, so a deploy-phase
    gate never drops what `build run` recorded, and re-running a deploy never duplicates itself.
    """
    existing = read_policy_results(build_path) or []
    merged = [entry for entry in existing if entry.policy_name != result.policy_name] + [result]
    write_policy_results(build_path, merged)
