#!/usr/bin/env python3
"""The rules for strata's container image, checked on `Dockerfile.cli` (docs/design/cli-image.md).

The image is only built in CI, so a change that drops git, unpins the build or slips Terraform into a published
variant would otherwise show only in a pipeline that uses the image.
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


def _stages() -> dict[str, list[str]]:
    """The instructions of each named stage (`FROM ... AS name`), its `FROM` line first."""
    stages: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in _instructions():
        match = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if match:
            current = stages.setdefault(match.group(1) or f"<unnamed {len(stages)}>", [])
        if current is not None:
            current.append(line)
    return stages


def _variant(name: str) -> list[str]:
    """What a published variant holds: its own stage plus, through `FROM <stage>`, every stage it builds on."""
    stages = _stages()
    lines = stages[name]
    base = lines[0].split()[1]
    return (_variant(base) if base in stages else []) + lines


def _apt_packages(instructions: list[str]) -> set[str]:
    packages: set[str] = set()
    for line in instructions:
        for match in re.finditer(r"apt-get install ([^&;]+)", line):
            packages |= {word for word in match.group(1).split() if not word.startswith("-")}
    return packages


def _copied_tools(instructions: list[str]) -> set[str]:
    """The file names `COPY --from` and `tar -x` put into the variant (`/usr/local/bin/helm` -> `helm`)."""
    tools = set()
    for line in instructions:
        if line.upper().startswith("COPY --FROM="):
            tools.add(line.split()[-1].rstrip("/").rsplit("/", 1)[-1])
        tools |= set(re.findall(r'tar -x\S* .*?"?linux-\$\{TARGETARCH\}/(\w+)"?', line))
        tools |= set(re.findall(r"ln -s \S+ /usr/local/bin/(\w+)", line))
    return tools


@pytest.mark.parametrize("variant", ["slim", "full"])
def test_every_variant_has_the_tools_strata_itself_calls(variant):
    """git for remotes, the commit SHA, audit push and GitOps; ssh for `auth.method: ssh_key`."""
    assert {"git", "openssh-client"} <= _apt_packages(_variant(variant))


def test_the_full_variant_has_the_pipeline_tools():
    assert {"tofu", "helm", "docker", "docker-compose", "trivy", "checkov"} <= _copied_tools(_variant("full"))


@pytest.mark.parametrize("tool", ["tofu", "helm", "docker", "docker-compose", "trivy", "checkov", "grype", "kubectl"])
def test_the_slim_variant_leaves_pipeline_tools_out(tool):
    slim = _variant("slim")
    assert tool not in _apt_packages(slim) | _copied_tools(slim)


@pytest.mark.parametrize("variant", ["slim", "full"])
def test_no_variant_ships_terraform(variant):
    """Terraform's BSL licence is not settled for a public image; the full image ships OpenTofu instead (ADR-0060)."""
    text = " ".join(_variant(variant)).lower()
    assert "terraform" not in _apt_packages(_variant(variant)) | _copied_tools(_variant(variant))
    assert "/terraform" not in text and "terraform_" not in text and "hashicorp/terraform" not in text


def test_the_default_target_is_slim():
    """`docker build` without `--target` builds the last stage: it must be slim, the image most users expect."""
    assert _instructions()[[i for i, line in enumerate(_instructions()) if line.upper().startswith("FROM ")][-1]] == (
        "FROM slim"
    )


def test_git_trusts_a_mounted_checkout():
    """A `docker run -v` checkout belongs to another user; without this `git rev-parse HEAD` fails silently."""
    assert any("git config --system --add safe.directory '*'" in line for line in _variant("slim"))


@pytest.mark.parametrize("variant", ["slim", "full"])
def test_the_user_is_not_root_and_has_a_home_directory(variant):
    lines = _variant(variant)
    [useradd] = [line for line in lines if "useradd" in line]
    assert "--create-home" in useradd or re.search(r"\s-m\b", useradd)
    users = [line.split()[1] for line in lines if line.upper().startswith("USER ")]
    assert users and users[-1] not in ("root", "0"), f"{variant} must end as a non-root user"


def test_every_image_input_is_pinned():
    """No `latest` and no bare tag on any `FROM`, so a build does not depend on the day it ran."""
    stage_names = set(_stages())
    for line in _instructions():
        if not line.upper().startswith("FROM "):
            continue
        image = line.split()[1]
        if image in stage_names:
            continue
        assert ":" in image and not image.endswith(":latest"), line
        assert re.search(r":v?\d+\.\d+(\.\d+)?", image), f"{line}: pin a version"


def test_the_build_is_reproducible():
    """The dependencies are installed from `uv.lock`, the versions CI tested; Checkov from a pinned requirement."""
    instructions = _instructions()
    assert any(line.startswith("COPY") and "uv.lock" in line for line in instructions)
    [sync] = [line for line in instructions if "uv sync" in line]
    assert "--locked" in sync or "--frozen" in sync
    requirements = (REPO_ROOT / "docker" / "checkov" / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^checkov==\d+\.\d+\.\d+$", requirements, re.MULTILINE)


def test_helm_is_checked_against_its_published_checksum():
    [helm] = [line for line in _variant("full") if "get.helm.sh" in line]
    assert "sha256sum -c" in helm
