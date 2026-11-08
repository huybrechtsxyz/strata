#!/usr/bin/env python3
"""Tests for the registry chart cache (docs/work/helm-registry-chart-checks.md, Phase 1)."""

import functools
import gzip
import http.server
import io
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest
import yaml

from strata.integrations.helm import HelmIntegration
from strata.integrations.helm_cache import (
    DIGEST_CHANGED,
    PULL_FAILED,
    ChartPullError,
    Subchart,
    archive_digest,
    cached_pin,
    entry_path,
    is_exact_version,
    pull_chart,
)
from strata.utils.transport import CommandResult


def _chart_archive(path: Path, name: str, version: str, *, app_version: str | None = "1.0", values: str = "") -> Path:
    """A packaged chart, as `helm package` writes it, with one subchart archive under `charts/`.

    The gzip timestamps are fixed, so the same chart is the same bytes on every pull, as a registry serves it.
    """

    def add(tar: tarfile.TarFile, member: str, text: str) -> None:
        data = text.encode()
        info = tarfile.TarInfo(member)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    sub = io.BytesIO()
    with gzip.GzipFile(fileobj=sub, mode="wb", mtime=0) as packed, tarfile.open(fileobj=packed, mode="w") as tar:
        add(tar, "postgresql/Chart.yaml", "apiVersion: v2\nname: postgresql\nversion: 15.5.38\n")
    chart_yaml = {"apiVersion": "v2", "name": name, "version": version}
    if app_version is not None:
        chart_yaml["appVersion"] = app_version
    archive = path / f"{name}-{version}.tgz"
    with gzip.GzipFile(archive, "wb", mtime=0) as packed, tarfile.open(fileobj=packed, mode="w") as tar:
        add(tar, f"{name}/Chart.yaml", yaml.safe_dump(chart_yaml))
        add(tar, f"{name}/values.yaml", values)
        info = tarfile.TarInfo(f"{name}/charts/postgresql-15.5.38.tgz")
        info.size = len(sub.getvalue())
        tar.addfile(info, io.BytesIO(sub.getvalue()))
    return archive


class _FakeHelm:
    """`helm pull` that writes a chart archive at `version` into `--destination`."""

    def __init__(self, version: str = "1.2.3", *, fail: str | None = None, values: str = "") -> None:
        self.version = version
        self.fail = fail
        self.values = values
        self.calls: list[list[str]] = []

    def run(self, *args: str, **kwargs) -> CommandResult:
        self.calls.append(list(args))
        if self.fail is not None:
            return CommandResult(returncode=1, stdout="", stderr=self.fail)
        destination = Path(args[args.index("--destination") + 1])
        _chart_archive(destination, "authentik", self.version, values=self.values)
        return CommandResult(returncode=0, stdout="", stderr="")


def _pull(helm: _FakeHelm, cache: Path, version: str | None = "1.2.3", **kwargs):
    return pull_chart(
        helm,  # type: ignore[arg-type]
        "goauthentik/authentik",
        chart="authentik",
        version=version,
        cache_root=cache,
        source="goauthentik",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("version", "exact"),
    [("1.2.3", True), ("v1.2.3", True), ("1.2.3-rc.1+build.5", True), ("^1.2.0", False), ("1.2", False), (None, False)],
)
def test_an_exact_version_is_one_semver_version(version, exact):
    assert is_exact_version(version) is exact


def test_a_pulled_chart_is_stored_under_its_exact_version(tmp_path: Path):
    helm = _FakeHelm("2026.5.2")

    pin = _pull(helm, tmp_path, version="^2026.5.0")

    entry = entry_path(tmp_path, "goauthentik", "authentik", "2026.5.2")
    assert pin.version == "2026.5.2"
    assert pin.archive == entry / "authentik-2026.5.2.tgz"
    assert pin.chart_dir == entry / "authentik"
    assert pin.digest == archive_digest(pin.archive)
    assert pin.digest.startswith("sha256:")
    assert pin.app_version == "1.0"
    assert pin.subcharts == (Subchart(name="postgresql", version="15.5.38"),)
    assert helm.calls == [
        ["pull", "goauthentik/authentik", "--destination", helm.calls[0][3], "--version", "^2026.5.0"]
    ]
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".pull-")]


def test_no_version_pulls_without_a_version_flag(tmp_path: Path):
    helm = _FakeHelm()

    _pull(helm, tmp_path, version=None)

    assert "--version" not in helm.calls[0]


def test_a_cached_chart_is_found_without_helm(tmp_path: Path):
    pulled = _pull(_FakeHelm(), tmp_path)

    assert cached_pin(tmp_path, "goauthentik", "authentik", "1.2.3") == pulled
    assert cached_pin(tmp_path, "goauthentik", "authentik", "9.9.9") is None
    assert cached_pin(tmp_path, "other", "authentik", "1.2.3") is None


def test_an_incomplete_entry_is_not_a_cache_hit_and_is_replaced(tmp_path: Path):
    entry = entry_path(tmp_path, "goauthentik", "authentik", "1.2.3")
    entry.mkdir(parents=True)
    (entry / "leftover").write_text("x")

    assert cached_pin(tmp_path, "goauthentik", "authentik", "1.2.3") is None
    pin = _pull(_FakeHelm(), tmp_path)

    assert not (entry / "leftover").exists()
    assert pin.archive.is_file()


def test_pulling_a_version_already_cached_keeps_the_cached_archive(tmp_path: Path):
    first = _pull(_FakeHelm(), tmp_path)

    again = _pull(_FakeHelm(), tmp_path)

    assert again == first


def test_a_registry_serving_new_content_under_a_cached_version_is_rejected(tmp_path: Path):
    first = _pull(_FakeHelm(), tmp_path)

    with pytest.raises(ChartPullError) as raised:
        _pull(_FakeHelm(values="changed: true\n"), tmp_path)

    assert raised.value.code == DIGEST_CHANGED
    assert cached_pin(tmp_path, "goauthentik", "authentik", "1.2.3") == first


def test_an_expected_digest_that_differs_is_rejected_and_not_stored(tmp_path: Path):
    with pytest.raises(ChartPullError) as raised:
        _pull(_FakeHelm(), tmp_path, expected_digest="sha256:" + "0" * 64)

    assert raised.value.code == DIGEST_CHANGED
    assert "but the build pinned sha256:000" in str(raised.value)
    assert cached_pin(tmp_path, "goauthentik", "authentik", "1.2.3") is None


def test_a_failed_pull_reports_helms_error(tmp_path: Path):
    with pytest.raises(ChartPullError) as raised:
        _pull(_FakeHelm(fail='Error: chart "authentik" version "1.2.3" not found'), tmp_path)

    assert raised.value.code == PULL_FAILED
    assert str(raised.value) == (
        'could not pull chart \'authentik\' (1.2.3) — Error: chart "authentik" version "1.2.3" not found'
    )
    assert not list(tmp_path.glob("goauthentik"))


def test_an_archive_with_more_than_one_top_level_directory_is_not_a_chart(tmp_path: Path):
    class _Bad(_FakeHelm):
        def run(self, *args: str, **kwargs) -> CommandResult:
            destination = Path(args[args.index("--destination") + 1])
            with tarfile.open(destination / "authentik-1.2.3.tgz", "w:gz") as tar:
                for name in ("a/Chart.yaml", "b/Chart.yaml"):
                    info = tarfile.TarInfo(name)
                    tar.addfile(info, io.BytesIO(b""))
            return CommandResult(returncode=0, stdout="", stderr="")

    with pytest.raises(ChartPullError, match="is not a chart archive"):
        _pull(_Bad(), tmp_path)


@pytest.mark.skipif(not shutil.which("helm"), reason="helm is not installed")
def test_real_helm_pulls_a_packaged_chart_into_the_cache(tmp_path: Path):
    """`helm package` a chart with a subchart, serve it over local HTTP, and pull it into the cache for real."""
    source = tmp_path / "src" / "demo"
    (source / "charts" / "child").mkdir(parents=True)
    (source / "Chart.yaml").write_text("apiVersion: v2\nname: demo\nversion: 0.3.1\nappVersion: '4.5'\n")
    (source / "values.yaml").write_text("replicaCount: 1\n")
    (source / "charts" / "child" / "Chart.yaml").write_text("apiVersion: v2\nname: child\nversion: 2.0.0\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    helm_home = tmp_path / "helm-home"
    env = {
        "HELM_REPOSITORY_CONFIG": str(helm_home / "repositories.yaml"),
        "HELM_REPOSITORY_CACHE": str(helm_home / "cache"),
        "HELM_REGISTRY_CONFIG": str(helm_home / "registry.json"),
        "HELM_CACHE_HOME": str(helm_home / "cache-home"),
    }

    def helm(*args: str) -> None:
        subprocess.run(["helm", *args], check=True, capture_output=True, env={**os.environ, **env}, timeout=60)

    helm("package", str(source), "--destination", str(repo))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(repo)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        helm("repo", "index", str(repo), "--url", url)
        helm("repo", "add", "local", url)

        pin = pull_chart(
            HelmIntegration(),
            "local/demo",
            chart="demo",
            version="~0.3.0",
            cache_root=tmp_path / "cache",
            source="local",
            env=env,
        )
    finally:
        server.shutdown()

    assert pin.version == "0.3.1"
    assert pin.app_version == "4.5"
    assert pin.subcharts == (Subchart(name="child", version="2.0.0"),)
    assert pin.digest == archive_digest(repo / "demo-0.3.1.tgz")
    assert (pin.chart_dir / "values.yaml").read_text() == "replicaCount: 1\n"
    assert cached_pin(tmp_path / "cache", "local", "demo", "0.3.1") == pin


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's parameter name
        del format, args
