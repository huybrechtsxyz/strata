#!/usr/bin/env python3
"""Tests for `policy_results.py` (docs/work/audit-trail.md's
`ManifestPolicyResultModel` population design) — `write_policy_results()`
and `read_policy_results()` in isolation from `build_run()`/
`finalize_and_distribute_deploy_audit()`."""

from pathlib import Path

from strata.controllers.policy_results import read_policy_results, write_policy_results
from strata.models.audit_manifest_model import ManifestPolicyResultModel


def _result(**overrides: object) -> ManifestPolicyResultModel:
    fields: dict[str, object] = {
        "policy_name": "cve_policy",
        "policy_type": "cve_max_severity",
        "phase": "build",
        "enforcement": "deny",
        "passed": False,
        "violations": ["1 CRITICAL finding(s) exceed max_count=0"],
    }
    fields.update(overrides)
    return ManifestPolicyResultModel.model_validate(fields)


def test_write_policy_results_skips_writing_when_results_is_empty(tmp_path: Path):
    write_policy_results(tmp_path, [])

    assert not (tmp_path / "policy_results.json").exists()


def test_read_policy_results_round_trips_a_written_file(tmp_path: Path):
    result = _result()

    write_policy_results(tmp_path, [result])
    round_tripped = read_policy_results(tmp_path)

    assert round_tripped == [result]


def test_read_policy_results_is_none_when_file_is_missing(tmp_path: Path):
    assert read_policy_results(tmp_path) is None
