#!/usr/bin/env python3
"""Registry Helm charts pinned by `build run` and verified by the deploy (docs/work/helm-registry-chart-checks.md).

A pinned chart lives at `<cache>/<source>/<chart>/<version>/` as the archive Helm pulled plus that archive unpacked.
The digest is strata's own SHA-256 of the archive, so a cache hit needs no `helm` (ADR-0054). A pull lands in a
temporary directory inside the cache and is renamed into place, so an interrupted pull leaves no partial entry.
"""

import hashlib
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from strata.integrations.base import Integration
from strata.integrations.errors import IntegrationError

#: An exact SemVer 2 version, as Helm writes it in `Chart.yaml` (an optional leading `v`). Anything else is a
#: constraint only the registry can resolve.
_EXACT_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

PULL_FAILED = "helm_chart_pull_failed"
DIGEST_CHANGED = "helm_chart_digest_changed"


class ChartPullError(IntegrationError):
    """A chart could not be pulled, or the pulled archive is not the one that was pinned.

    `code` is the diagnostic code the caller reports: `helm_chart_pull_failed` or `helm_chart_digest_changed`.
    """

    def __init__(self, message: str, *, code: str = PULL_FAILED) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Subchart:
    """A dependency packaged in a chart's `charts/` directory, at its exact version."""

    name: str
    version: str


@dataclass(frozen=True)
class ChartPin:
    """A registry chart in the cache: what `build run` records in `meta.yaml` and the deploy installs."""

    name: str
    version: str
    digest: str
    archive: Path
    chart_dir: Path
    app_version: str | None = None
    subcharts: tuple[Subchart, ...] = ()


def is_exact_version(version: str | None) -> bool:
    """True for one exact chart version (`1.2.3`), False for a range or nothing."""
    return version is not None and _EXACT_VERSION.match(version) is not None


def archive_digest(path: Path) -> str:
    """`sha256:<hex>` of the file at `path`."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def entry_path(cache_root: Path, source: str, chart: str, version: str) -> Path:
    """Where the cache keeps `chart` at `version` from `source` (a remote name or a repository alias)."""
    return cache_root / source / chart / version


def cached_pin(cache_root: Path, source: str, chart: str, version: str) -> ChartPin | None:
    """The cached chart for an exact `version`, or `None` when there is no complete entry."""
    entry = entry_path(cache_root, source, chart, version)
    if not entry.is_dir():
        return None
    archives = list(entry.glob("*.tgz"))
    chart_dirs = [path for path in entry.iterdir() if (path / "Chart.yaml").is_file()]
    if len(archives) != 1 or len(chart_dirs) != 1:
        return None
    return _pin(chart, archives[0], chart_dirs[0])


def pull_chart(
    integration: Integration,
    ref: str,
    *,
    chart: str,
    version: str | None,
    cache_root: Path,
    source: str,
    env: Mapping[str, str] | None = None,
    timeout: int = 300,
    expected_digest: str | None = None,
) -> ChartPin:
    """`helm pull` `ref` into the cache and return its pin.

    `version` may be a range or `None`; the entry is keyed by the version the archive turns out to have. With
    `expected_digest` (a deploy verifying a pinned build), a different archive is rejected and not stored. An entry
    that already exists for the pulled version keeps its archive; a different digest there is rejected too, since
    the registry then serves new content under a version already pinned.

    Raises:
        ChartPullError: the pull failed, the archive is not a chart, or its digest differs.
    """
    wanted = f"chart '{chart}'" + (f" ({version})" if version else "")
    cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pull-", dir=cache_root) as tmp:
        staging = Path(tmp) / "entry"
        staging.mkdir()
        args = ["pull", ref, "--destination", str(staging)]
        if version:
            args += ["--version", version]
        result = integration.run(*args, env=env, timeout=timeout)
        if not result.is_successful:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise ChartPullError(f"could not pull {wanted} — {detail}")
        archives = list(staging.glob("*.tgz"))
        if len(archives) != 1:
            raise ChartPullError(f"pulling {wanted} produced no single chart archive")
        chart_dir = _unpack(archives[0], staging, wanted)
        pulled = _pin(chart, archives[0], chart_dir)
        if expected_digest is not None and pulled.digest != expected_digest:
            raise ChartPullError(
                f"{wanted} at {pulled.version} is {pulled.digest}, but the build pinned {expected_digest}",
                code=DIGEST_CHANGED,
            )

        entry = entry_path(cache_root, source, chart, pulled.version)
        existing = cached_pin(cache_root, source, chart, pulled.version)
        if existing is not None:
            if existing.digest != pulled.digest:
                raise ChartPullError(
                    f"{wanted} at {pulled.version} is now {pulled.digest}, but {entry} holds {existing.digest}",
                    code=DIGEST_CHANGED,
                )
            return existing
        if entry.exists():
            shutil.rmtree(entry)  # an incomplete entry from before entries were renamed into place
        entry.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, entry)
    stored = cached_pin(cache_root, source, chart, pulled.version)
    assert stored is not None  # just renamed into place
    return stored


def _unpack(archive: Path, destination: Path, wanted: str) -> Path:
    """Unpack `archive` (one top-level chart directory) into `destination` and return that directory."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tops = {member.name.split("/", 1)[0] for member in tar.getmembers()}
            if len(tops) != 1:
                raise ChartPullError(f"{wanted}: {archive.name} is not a chart archive (one top-level directory)")
            tar.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise ChartPullError(f"{wanted}: {archive.name} could not be unpacked — {exc}") from exc
    chart_dir = destination / tops.pop()
    if not (chart_dir / "Chart.yaml").is_file():
        raise ChartPullError(f"{wanted}: {archive.name} has no Chart.yaml")
    return chart_dir


def _pin(chart: str, archive: Path, chart_dir: Path) -> ChartPin:
    meta = _read_yaml((chart_dir / "Chart.yaml").read_text(encoding="utf-8"))
    app_version = meta.get("appVersion")
    return ChartPin(
        name=chart,
        version=str(meta.get("version", "")),
        digest=archive_digest(archive),
        archive=archive,
        chart_dir=chart_dir,
        app_version=str(app_version) if app_version is not None else None,
        subcharts=subcharts(chart_dir / "charts"),
    )


def subcharts(charts: Path) -> tuple[Subchart, ...]:
    """Name and exact version of every chart packaged in `charts/`, unpacked or as an archive."""
    if not charts.is_dir():
        return ()
    found: list[Subchart] = []
    for path in sorted(charts.iterdir()):
        if (path / "Chart.yaml").is_file():
            meta = _read_yaml((path / "Chart.yaml").read_text(encoding="utf-8"))
        elif path.suffix == ".tgz":
            meta = _archive_chart_yaml(path)
        else:
            continue
        if meta.get("name") and meta.get("version"):
            found.append(Subchart(name=str(meta["name"]), version=str(meta["version"])))
    return tuple(found)


def _archive_chart_yaml(archive: Path) -> dict[str, Any]:
    """The top-level `Chart.yaml` inside a packaged chart, or `{}`."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/")
                if len(parts) == 2 and parts[1] == "Chart.yaml" and member.isfile():
                    handle = tar.extractfile(member)
                    return _read_yaml(handle.read().decode("utf-8")) if handle is not None else {}
    except (tarfile.TarError, OSError, UnicodeDecodeError):
        return {}
    return {}


def read_chart_yaml(path: Path) -> dict[str, Any]:
    """A chart's YAML file (`Chart.yaml`, `Chart.lock`) as a mapping; `{}` when absent, unreadable or not a mapping."""
    try:
        return _read_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def _read_yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
