#!/usr/bin/env python3
"""Tests for the registry chart cache (docs/design/helm-chart-deployment.md, "Registry charts")."""

import functools
import gzip
import http.server
import io
import os
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

import pytest
import yaml

from strata.integrations import helm_cache
from strata.integrations.helm import HelmIntegration
from strata.integrations.helm_cache import (
    DIGEST_CHANGED,
    PULL_FAILED,
    STALE_STAGING_SECONDS,
    ChartPullError,
    Subchart,
    archive_digest,
    cached_pin,
    entry_path,
    is_exact_version,
    pull_chart,
    remove_stale_staging,
)
from strata.utils.file_release import RELEASE_WAITS
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


# ---------------------------------------------------------------------------
# Storing the entry while another process holds a file in it (a virus scanner, on Windows)
# ---------------------------------------------------------------------------


def _replace_refusing(monkeypatch, times: int | None, errno: int = 13) -> list[float]:
    """`os.replace` in the cache fails `times` times (`None`: always) with `errno` (13, EACCES, is what Python
    raises as `PermissionError`); returns the waits slept."""
    real_replace = os.replace
    refused = {"count": 0}

    def replace(source, target):
        if times is None or refused["count"] < times:
            refused["count"] += 1
            raise OSError(errno, os.strerror(errno), str(source))
        real_replace(source, target)

    sleeps: list[float] = []
    monkeypatch.setattr(helm_cache.os, "replace", replace)
    monkeypatch.setattr(helm_cache.time, "sleep", sleeps.append)
    return sleeps


def test_a_rename_refused_while_a_file_is_held_open_is_retried(tmp_path: Path, monkeypatch):
    sleeps = _replace_refusing(monkeypatch, times=2)

    pin = _pull(_FakeHelm(), tmp_path)

    assert pin.archive.is_file()
    assert sleeps == list(RELEASE_WAITS[:2])


def test_a_rename_refused_for_good_fails_the_pull_after_the_waits(tmp_path: Path, monkeypatch):
    sleeps = _replace_refusing(monkeypatch, times=None)

    with pytest.raises(ChartPullError, match="could not be stored"):
        _pull(_FakeHelm(), tmp_path)

    assert sleeps == list(RELEASE_WAITS)


def test_another_storage_error_is_not_retried(tmp_path: Path, monkeypatch):
    sleeps = _replace_refusing(monkeypatch, times=None, errno=28)  # ENOSPC: waiting does not free the disk

    with pytest.raises(ChartPullError, match="could not be stored"):
        _pull(_FakeHelm(), tmp_path)

    assert sleeps == []


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to rename a directory with a file open in it")
def test_a_pull_waits_for_a_scanner_to_let_go_of_the_new_archive(tmp_path: Path):
    """The flaky failure under a full parallel run: a process reading the archive just written (as Defender does)
    makes renaming the staging directory fail with "Access is denied" until it closes the file."""

    class _ScannedHelm(_FakeHelm):
        def run(self, *args: str, **kwargs) -> CommandResult:
            result = super().run(*args, **kwargs)
            destination = Path(args[args.index("--destination") + 1])
            handle = open(next(destination.glob("*.tgz")), "rb")  # noqa: SIM115 - closed by the timer
            threading.Timer(0.3, handle.close).start()
            return result

    pin = _pull(_ScannedHelm(), tmp_path)

    assert pin.archive.is_file()


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


def _lock_staged_archives(monkeypatch) -> None:
    """Windows refuses to delete a file another process holds open, as a virus scanner does a newly written archive."""
    real_unlink = os.unlink

    def unlink(path, *args, dir_fd=None, **kwargs):
        # Windows' `shutil.rmtree` unlinks by full path; Linux's unlinks the bare file name relative to an open
        # directory (`dir_fd`). These tests remove no archive but a staged one, so either form is the locked file.
        if str(path).endswith(".tgz") and (".pull-" in str(path) or dir_fd is not None):
            raise PermissionError(13, "The process cannot access the file because it is being used", str(path))
        return real_unlink(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)


def test_a_staging_directory_that_cannot_be_removed_does_not_hide_a_digest_change(tmp_path: Path, monkeypatch):
    _lock_staged_archives(monkeypatch)

    with pytest.raises(ChartPullError) as raised:
        _pull(_FakeHelm(), tmp_path, expected_digest="sha256:" + "0" * 64)

    assert raised.value.code == DIGEST_CHANGED
    assert [path.name.startswith(".pull-") for path in tmp_path.iterdir()] == [True]
    monkeypatch.undo()
    pin = _pull(_FakeHelm(), tmp_path)
    assert cached_pin(tmp_path, "goauthentik", "authentik", "1.2.3") == pin, "a leftover is never read as an entry"


def _staging_dir(cache: Path, name: str, *, age: float) -> Path:
    path = cache / name
    (path / "entry").mkdir(parents=True)
    (path / "entry" / "authentik-1.2.3.tgz").write_bytes(b"x")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def test_a_pull_removes_stale_staging_directories_and_keeps_recent_ones(tmp_path: Path):
    stale = _staging_dir(tmp_path, ".pull-old", age=STALE_STAGING_SECONDS + 60)
    recent = _staging_dir(tmp_path, ".pull-running", age=60)
    other = _staging_dir(tmp_path, "goauthentik", age=STALE_STAGING_SECONDS + 60)

    _pull(_FakeHelm(), tmp_path)

    assert not stale.exists()
    assert recent.is_dir(), "another process may still be pulling into it"
    assert other.is_dir(), "only .pull-* directories are staging"


def test_a_staging_directory_that_is_still_locked_is_left_for_a_later_pull(tmp_path: Path, monkeypatch):
    stale = _staging_dir(tmp_path, ".pull-old", age=STALE_STAGING_SECONDS + 60)
    _lock_staged_archives(monkeypatch)

    assert remove_stale_staging(tmp_path) == []
    assert stale.is_dir()

    monkeypatch.undo()
    assert remove_stale_staging(tmp_path) == [stale]
    assert not stale.exists()


def test_an_entry_that_cannot_be_stored_is_a_pull_failure(tmp_path: Path, monkeypatch):
    def replace(source, destination):
        raise PermissionError(13, "Access is denied", str(source))

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # the waits for a release that never comes

    with pytest.raises(ChartPullError) as raised:
        _pull(_FakeHelm(), tmp_path)

    assert raised.value.code == PULL_FAILED
    assert str(raised.value).startswith("chart 'authentik' (1.2.3) at 1.2.3 could not be stored in ")
    assert "Access is denied" in str(raised.value)
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
@pytest.mark.slow
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
