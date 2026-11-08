#!/usr/bin/env python3
"""Tests for planning a local chart's dependencies (docs/work/helm-chart-dependencies.md, Phase 1)."""

from pathlib import Path

import pytest

from strata.integrations.helm import check_configuration_keys
from strata.integrations.helm_dependencies import (
    DEPENDENCY_MISSING,
    LOCK_OUT_OF_SYNC,
    REPOSITORY_NAME,
    Action,
    Dependency,
    DependencyRecord,
    dependency_list_problems,
    plan_dependencies,
    read_dependencies,
    values_keys,
)

_REPO = "https://charts.example.com"


def _chart(tmp_path: Path, dependencies: str, *, lock: str | None = None, committed: dict[str, str] | None = None):
    chart = tmp_path / "app"
    chart.mkdir()
    (chart / "Chart.yaml").write_text(f"apiVersion: v2\nname: app\nversion: 1.0.0\ndependencies:\n{dependencies}")
    if lock is not None:
        (chart / "Chart.lock").write_text(lock)
    for name, version in (committed or {}).items():
        (chart / "charts" / name).mkdir(parents=True)
        (chart / "charts" / name / "Chart.yaml").write_text(f"apiVersion: v2\nname: {name}\nversion: {version}\n")
    return chart


def _actions(chart: Path) -> list[tuple[str, Action, str | None, str | None]]:
    return [(p.dependency.name, p.action, p.version, p.code) for p in plan_dependencies(chart)]


def test_dependencies_are_read_with_repository_and_alias(tmp_path: Path):
    chart = _chart(tmp_path, f"  - name: redis\n    version: ~19.0\n    repository: {_REPO}\n    alias: cache\n")

    assert read_dependencies(chart) == [Dependency(name="redis", version="~19.0", repository=_REPO, alias="cache")]


def test_a_v1_chart_reads_requirements_yaml(tmp_path: Path):
    chart = tmp_path / "old"
    chart.mkdir()
    (chart / "Chart.yaml").write_text("apiVersion: v1\nname: old\nversion: 1.0.0\n")
    (chart / "requirements.yaml").write_text(
        f"dependencies:\n  - name: redis\n    version: 1.0.0\n    repository: {_REPO}\n"
    )

    assert [d.name for d in read_dependencies(chart)] == ["redis"]


def test_without_a_lock_the_declared_version_is_pulled_exact_or_range(tmp_path: Path):
    chart = _chart(
        tmp_path,
        f"  - name: sepv\n    version: 99.99.343\n    repository: {_REPO}\n"
        f"  - name: redis\n    version: ~19.0\n    repository: {_REPO}\n",
    )

    assert _actions(chart) == [
        ("sepv", Action.PULL, "99.99.343", None),
        ("redis", Action.PULL, "~19.0", None),
    ]


def test_a_committed_lock_decides_the_version(tmp_path: Path):
    chart = _chart(
        tmp_path,
        f"  - name: redis\n    version: ~19.0\n    repository: {_REPO}\n",
        lock=f"dependencies:\n- name: redis\n  repository: {_REPO}\n  version: 19.0.4\ndigest: sha256:x\n",
    )

    assert _actions(chart) == [("redis", Action.PULL, "19.0.4", None)]


@pytest.mark.parametrize(
    "lock",
    [
        "dependencies:\n- name: other\n  repository: https://x\n  version: 1.0.0\n",
        "dependencies:\n- name: redis\n  repository: https://elsewhere.example.com\n  version: 19.0.4\n",
    ],
)
def test_a_lock_that_lacks_the_dependency_as_declared_is_out_of_sync(tmp_path: Path, lock: str):
    chart = _chart(tmp_path, f"  - name: redis\n    version: ~19.0\n    repository: {_REPO}\n", lock=lock)

    [planned] = plan_dependencies(chart)

    assert (planned.action, planned.code) == (Action.ERROR, LOCK_OUT_OF_SYNC)
    assert "run 'helm dependency update' and commit Chart.lock" in str(planned.message)


def test_a_dependency_already_in_charts_is_committed_whatever_its_repository(tmp_path: Path):
    chart = _chart(
        tmp_path,
        f"  - name: redis\n    version: ~19.0\n    repository: {_REPO}\n  - name: local\n    version: 1.0.0\n",
        committed={"redis": "19.0.1", "local": "1.0.0"},
    )

    assert _actions(chart) == [("redis", Action.COMMITTED, None, None), ("local", Action.COMMITTED, None, None)]


def test_a_file_dependency_is_copied(tmp_path: Path):
    chart = _chart(tmp_path, "  - name: common\n    version: 0.1.0\n    repository: file://../common\n")

    assert _actions(chart) == [("common", Action.FILE, None, None)]


def test_no_repository_and_nothing_committed_is_missing(tmp_path: Path):
    chart = _chart(tmp_path, "  - name: policyReporter\n    version: 1.0.0\n")

    [planned] = plan_dependencies(chart)

    assert (planned.action, planned.code) == (Action.ERROR, DEPENDENCY_MISSING)


@pytest.mark.parametrize("repository", ["'@bitnami'", "alias:bitnami"])
def test_a_helm_repository_name_is_refused(tmp_path: Path, repository: str):
    chart = _chart(tmp_path, f"  - name: redis\n    version: 1.0.0\n    repository: {repository}\n")

    [planned] = plan_dependencies(chart)

    assert (planned.action, planned.code) == (Action.ERROR, REPOSITORY_NAME)
    assert "use the repository URL instead" in str(planned.message)


def test_dependency_list_problems_reads_helms_table():
    stdout = (
        "NAME  \tVERSION\tREPOSITORY           \tSTATUS       \n"
        "sub   \t~1.2.0 \thttp://127.0.0.1:8765\twrong version\n"
        "common\t0.1.0  \tfile://../common     \tunpacked     \n"
        "redis \t19.0.1 \thttps://x            \tok           \n"
        "gone  \t1.0.0  \thttps://x            \tmissing      \n"
        "\n"
    )

    assert dependency_list_problems(stdout) == [("sub", "wrong version"), ("gone", "missing")]


def test_a_record_lists_only_what_it_knows():
    record = DependencyRecord("common", "0.1.0", "file", repository="file://../common")

    assert record.to_meta() == {
        "name": "common",
        "version": "0.1.0",
        "repository": "file://../common",
        "origin": "file",
    }


def test_subchart_keys_are_names_aliases_and_global(tmp_path: Path):
    chart = _chart(
        tmp_path,
        f"  - name: redis\n    version: 1.0.0\n    repository: {_REPO}\n    alias: cache\n"
        f"  - name: sepv\n    version: 1.0.0\n    repository: {_REPO}\n",
    )

    assert values_keys(chart) == {"global", "cache", "sepv"}


def test_the_key_check_passes_subchart_keys_on():
    findings = check_configuration_keys(
        {"sepv": {"enabled": True}, "global": {"x": 1}, "replicaCnt": 2},
        {"replicaCount": 1},
        {"sepv", "global"},
    )

    assert len(findings) == 1 and "'replicaCnt'" in findings[0]
