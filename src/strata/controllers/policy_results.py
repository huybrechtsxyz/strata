#!/usr/bin/env python3
"""Policy evaluation results sidecar (`build_path/policy_results.json`) —
docs/work/audit-trail.md's "DeploymentManifestModel.policy_results
population" design. The one shared file-format contract between
`build run` (writes) and `deploy run`'s audit finalize step (reads) —
matches the existing `resolved.yaml`/`sbom.json` hand-off precedent
between the two commands.

Scoped to build-phase policies only (`cve_policy` today) — validate-phase
policies (`tenant_zone`/`path_convention`) are explicitly out of scope for
this pass, since `strata validate` has no `build_path` concept and no
enforced 1:1 relationship to any one later `deploy run` invocation.
"""

import json
from pathlib import Path

from strata.models.audit_manifest_model import ManifestPolicyResultModel

#: Public — both `build_controller.py` (writes) and `audit_run.py` (reads)
#: must agree on this exact filename.
POLICY_RESULTS_FILENAME = "policy_results.json"


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
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ManifestPolicyResultModel.model_validate(item) for item in payload["policy_results"]]
