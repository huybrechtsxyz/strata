#!/usr/bin/env python3
"""The dependencies of a local Helm chart, planned and recorded by `build run` (docs/design/helm-chart-deployment.md).

Strata fills the build copy's `charts/` itself instead of running `helm dependency build`: without a `Chart.lock`
that command writes one (ADR-0054 rules out inventing a tool's lock), with one it needs every HTTP repository in the
Helm configuration, and it cannot follow a `file://` path once the chart is copied away from its siblings. This
module is the pure part: what each dependency needs, and the record `meta.yaml` keeps of what was placed.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from strata.integrations.helm_cache import Subchart, read_chart_yaml, subcharts

LOCK_OUT_OF_SYNC = "helm_chart_dependency_lock_out_of_sync"
DEPENDENCY_MISSING = "helm_chart_dependency_missing"
WRONG_VERSION = "helm_chart_dependency_wrong_version"
REPOSITORY_NAME = "helm_chart_dependency_repository_name"
OUTSIDE_SOURCE = "helm_chart_dependency_outside_source"
DEPENDENCY_NOT_PINNED = "helm_chart_dependency_not_pinned"

#: `helm dependency list` statuses that mean the dependency is in place.
_IN_PLACE = frozenset({"ok", "unpacked"})


class Action(Enum):
    """What `build run` does for one dependency."""

    COMMITTED = "committed"  # already in the source's charts/
    FILE = "file"  # copy the file:// directory
    PULL = "pull"  # pull `version` into the cache and copy the archive
    ERROR = "error"  # `code` and `message` say why not


@dataclass(frozen=True)
class Dependency:
    """One entry of `Chart.yaml`'s `dependencies` (or a v1 chart's `requirements.yaml`)."""

    name: str
    version: str | None
    repository: str | None
    alias: str | None = None


@dataclass(frozen=True)
class PlannedDependency:
    """A dependency and what to do for it. `version` is the one to pull: exact, locked, or a range to resolve."""

    dependency: Dependency
    action: Action
    version: str | None = None
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class DependencyRecord:
    """A dependency the build placed in `charts/` (or found there), as `meta.yaml`'s `chartDependencies` lists it."""

    name: str
    version: str
    origin: str  # pulled | cache | committed | file
    repository: str | None = None
    constraint: str | None = None
    digest: str | None = None

    def to_meta(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.constraint is not None:
            entry["versionConstraint"] = self.constraint
        if self.repository is not None:
            entry["repository"] = self.repository
        if self.digest is not None:
            entry["digest"] = self.digest
        entry["origin"] = self.origin
        return entry


def read_dependencies(chart_dir: Path) -> list[Dependency]:
    """The dependencies `chart_dir` declares, in `Chart.yaml` or (an `apiVersion: v1` chart) `requirements.yaml`."""
    entries = read_chart_yaml(chart_dir / "Chart.yaml").get("dependencies")
    if not entries:
        entries = read_chart_yaml(chart_dir / "requirements.yaml").get("dependencies")
    found: list[Dependency] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        found.append(
            Dependency(
                name=str(entry["name"]),
                version=_text(entry.get("version")),
                repository=_text(entry.get("repository")),
                alias=_text(entry.get("alias")),
            )
        )
    return found


def values_keys(chart_dir: Path) -> set[str]:
    """Top-level values keys that belong to subcharts: every dependency's name or alias, and `global`."""
    return {"global"} | {dependency.alias or dependency.name for dependency in read_dependencies(chart_dir)}


def plan_dependencies(chart_dir: Path) -> list[PlannedDependency]:
    """What each dependency of the chart at `chart_dir` needs (the table in docs/design/helm-chart-deployment.md)."""
    committed = {sub.name for sub in subcharts(chart_dir / "charts")}
    lock = _read_lock(chart_dir)
    planned: list[PlannedDependency] = []
    for dependency in read_dependencies(chart_dir):
        planned.append(_plan(dependency, committed, lock))
    return planned


def committed_subcharts(chart_dir: Path) -> dict[str, Subchart]:
    """The charts already in `chart_dir/charts/`, by name."""
    return {sub.name: sub for sub in subcharts(chart_dir / "charts")}


def dependency_list_problems(stdout: str) -> list[tuple[str, str]]:
    """`(name, status)` for every row of `helm dependency list` whose status is not `ok` or `unpacked`."""
    problems: list[tuple[str, str]] = []
    rows = [line for line in stdout.splitlines() if "\t" in line]
    for row in rows[1:]:  # the first is the NAME/VERSION/REPOSITORY/STATUS header
        columns = [column.strip() for column in row.split("\t")]
        name, status = columns[0], columns[-1]
        if name and status not in _IN_PLACE:
            problems.append((name, status))
    return problems


def _plan(dependency: Dependency, committed: set[str], lock: dict[str, dict[str, Any]] | None) -> PlannedDependency:
    repository = dependency.repository
    if dependency.name in committed:
        return PlannedDependency(dependency, Action.COMMITTED)
    if repository is None:
        return PlannedDependency(
            dependency,
            Action.ERROR,
            code=DEPENDENCY_MISSING,
            message=f"dependency '{dependency.name}' has no repository and is not in the chart's charts/ directory",
        )
    if repository.startswith(("@", "alias:")):
        return PlannedDependency(
            dependency,
            Action.ERROR,
            code=REPOSITORY_NAME,
            message=(
                f"dependency '{dependency.name}' names repository '{repository}', which depends on this machine's "
                "Helm configuration; use the repository URL instead"
            ),
        )
    if repository.startswith("file://"):
        return PlannedDependency(dependency, Action.FILE)
    if lock is not None:
        locked = lock.get(dependency.name)
        if locked is None or _text(locked.get("repository")) != repository or not locked.get("version"):
            return PlannedDependency(
                dependency,
                Action.ERROR,
                code=LOCK_OUT_OF_SYNC,
                message=(
                    f"dependency '{dependency.name}' is not in Chart.lock as declared in Chart.yaml; run "
                    "'helm dependency update' and commit Chart.lock"
                ),
            )
        return PlannedDependency(dependency, Action.PULL, version=str(locked["version"]))
    return PlannedDependency(dependency, Action.PULL, version=dependency.version)


def _read_lock(chart_dir: Path) -> dict[str, dict[str, Any]] | None:
    """`Chart.lock` (or `requirements.lock`) dependencies by name, or `None` when the chart commits no lock."""
    for name in ("Chart.lock", "requirements.lock"):
        path = chart_dir / name
        if path.is_file():
            entries = read_chart_yaml(path).get("dependencies") or []
            return {str(entry["name"]): entry for entry in entries if isinstance(entry, dict) and entry.get("name")}
    return None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
