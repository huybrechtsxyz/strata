#!/usr/bin/env python3
"""The rules for strata's container image, checked on `Dockerfile.cli` (docs/design/cli-image.md).

The image is only built in CI, so a change that drops git or unpins the build would otherwise show only in a
pipeline that uses the image.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile.cli"


def _instructions() -> list[str]:
    """Every instruction of the Dockerfile, its continuation lines joined, comments left out."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    logical = re.sub(r"\\\r?\n", " ", text)
    return [line.strip() for line in logical.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _runtime_stage() -> list[str]:
    """The instructions after the last `FROM`: what the published image holds."""
    instructions = _instructions()
    last_from = max(i for i, line in enumerate(instructions) if line.upper().startswith("FROM "))
    return instructions[last_from:]


def _apt_packages(instructions: list[str]) -> set[str]:
    packages: set[str] = set()
    for line in instructions:
        for match in re.finditer(r"apt-get install ([^&;]+)", line):
            packages |= {word for word in match.group(1).split() if not word.startswith("-")}
    return packages


def test_the_image_has_the_tools_strata_itself_calls():
    """git for remotes, the commit SHA, audit push and GitOps; ssh for `auth.method: ssh_key`."""
    assert {"git", "openssh-client"} <= _apt_packages(_runtime_stage())


@pytest.mark.parametrize("tool", ["terraform", "tofu", "helm", "docker", "kubectl", "trivy", "grype", "checkov"])
def test_the_image_leaves_provisioner_and_scanner_tools_to_the_pipeline(tool):
    runtime = " ".join(_runtime_stage()).lower()
    assert tool not in _apt_packages(_runtime_stage())
    assert f"/{tool}" not in runtime, f"{tool} is the pipeline's to bring, at the version it pins"


def test_git_trusts_a_mounted_checkout():
    """A `docker run -v` checkout belongs to another user; without this `git rev-parse HEAD` fails silently."""
    assert any("git config --system --add safe.directory '*'" in line for line in _runtime_stage())


def test_the_user_is_not_root_and_has_a_home_directory():
    runtime = _runtime_stage()
    [useradd] = [line for line in runtime if "useradd" in line]
    assert "--create-home" in useradd or re.search(r"\s-m\b", useradd)
    users = [line.split()[1] for line in runtime if line.upper().startswith("USER ")]
    assert users and users[-1] not in ("root", "0")


def test_the_build_is_reproducible():
    """`uv` pinned, and the dependencies installed from `uv.lock`, the versions CI tested."""
    instructions = _instructions()
    [uv_copy] = [line for line in instructions if "astral-sh/uv" in line]
    assert re.search(r"astral-sh/uv:\d+\.\d+\.\d+", uv_copy), uv_copy
    assert any(line.startswith("COPY") and "uv.lock" in line for line in instructions)
    [sync] = [line for line in instructions if "uv sync" in line]
    assert "--locked" in sync or "--frozen" in sync
