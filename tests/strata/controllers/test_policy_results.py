#!/usr/bin/env python3
"""Tests for `policy_results.py` (docs/work/audit-trail.md's
`ManifestPolicyResultModel` population design) — `write_policy_results()`
and `read_policy_results()` in isolation from `build_run()`/
`finalize_and_distribute_deploy_audit()`."""

from pathlib import Path

import pytest

from strata.controllers.policy_results import append_policy_result, read_policy_results, write_policy_results
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.utils.errors import SystemError


@pytest.mark.parametrize("content", ["{not json", '{"other": []}', '{"policy_results": [{"policy_name": 1}]}'])
def test_an_unreadable_file_is_a_system_error_naming_it(tmp_path: Path, content: str):
    (tmp_path / "policy_results.json").write_text(content, encoding="utf-8")
    with pytest.raises(SystemError, match="policy_results.json"):
        read_policy_results(tmp_path)


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


# ---------------------------------------------------------------------------
# append_policy_result() (docs/work/security-gates-design.md D9)
# ---------------------------------------------------------------------------

_GATE = {"policy_name": "release_gate_policy", "policy_type": "aikido", "phase": "deploy"}


def test_append_policy_result_creates_the_file(tmp_path: Path):
    gate = _result(**_GATE)
    append_policy_result(tmp_path, gate)
    assert read_policy_results(tmp_path) == [gate]


def test_append_policy_result_keeps_build_phase_entries(tmp_path: Path):
    build = _result()
    write_policy_results(tmp_path, [build])
    gate = _result(**_GATE)

    append_policy_result(tmp_path, gate)

    assert read_policy_results(tmp_path) == [build, gate]


def test_append_policy_result_replaces_an_entry_with_the_same_name(tmp_path: Path):
    build = _result()
    append_policy_result(tmp_path, build)
    append_policy_result(tmp_path, _result(**_GATE, passed=False))
    rerun = _result(**_GATE, passed=True, violations=[])

    append_policy_result(tmp_path, rerun)

    assert read_policy_results(tmp_path) == [build, rerun]
