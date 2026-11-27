#!/usr/bin/env python3
"""`build run` records the commit of the solution and of each git remote in `resolved.yaml`.

docs/design/remote-commit-provenance.md, "Collecting what the build took". Real `git` throughout: the record is only as
good as what `git` says.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from strata.controllers.build_controller import build_run
from strata.controllers.solution_context import open_solution
from strata.utils import layout

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
_COMMIT_VARS = (
    "STRATA_COMMIT_SHA",
    "GITHUB_SHA",
    "BUILD_SOURCEVERSION",
    "CI_COMMIT_SHA",
    "BITBUCKET_COMMIT",
    "GIT_COMMIT",
)


@pytest.fixture(autouse=True)
def _no_ci_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _COMMIT_VARS:
        monkeypatch.delenv(name, raising=False)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str = "commit") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repository(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    for name, text in files.items():
        _write(path, name, text)
    return _commit_all(path)


def _solution(
    tmp_path: Path,
    remotes: str,
    provisioners: list[tuple[str, str | None, str]],
    *,
    version: str = "",
    extra: dict[str, str] | None = None,
) -> tuple[Path, str]:
    """A git-committed solution whose first provisioner is the one execution step; the others are its `depends_on`.

    `provisioners` is `(name, remote or None, source_path)`.
    """
    root = tmp_path / "sln"
    _write(
        root,
        "strata.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec:\n"
        + (f"  remotes:\n{remotes}" if remotes else "  {}\n"),
    )
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n  type: local\n"
        "  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n  provider_type: local\n"
        "  resource_type: server\n  category: compute\n",
    )
    entries = []
    for index, (name, remote, source_path) in enumerate(provisioners):
        entry = f"    - name: {name}\n      tool: terraform\n      source:\n"
        if remote is not None:
            entry += f"        remote: {remote}\n"
        entry += f"        source_path: {source_path}\n"
        if index == 0 and len(provisioners) > 1:
            entry += "      depends_on:\n" + "".join(f"        - {other}\n" for other, _, _ in provisioners[1:])
        entries.append(entry)
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n  providers:\n    - p1\n"
        "  provisioners:\n" + "".join(entries) + "  execution:\n    - name: apply\n"
        f"      provisioner: {provisioners[0][0]}\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  workspace: main\n"
        "  environments:\n    - prd\n" + ("  version: prd\n" if version else ""),
    )
    if version:
        _write(root, "version.yaml", version)
    for relative, text in (extra or {}).items():
        _write(root, relative, text)
    _write(root, ".gitignore", ".strata/\n")
    root_head = _repository(root, {})
    return root, root_head


def _build(root: Path, build_path: Path) -> tuple[dict[str, Any], Any]:
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    diagnostics = build_run(context, "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    return yaml.safe_load((build_path / "resolved.yaml").read_text(encoding="utf-8")), diagnostics


def _codes(diagnostics: Any) -> list[str]:
    return [
        d.code for d in diagnostics.warnings if d.code in ("remote_checkout_dirty", "audit_remote_commit_unavailable")
    ]


_PATH_REMOTE = (
    "    - name: infra\n      type: git\n      url: https://git.example.com/infra.git\n      path: ../infra\n"
)


def test_a_path_remote_and_the_solution_are_recorded(tmp_path: Path):
    infra_head = _repository(tmp_path / "infra", {"infra/main.tf": "# v1\n"})
    root, root_head = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra")])

    resolved, diagnostics = _build(root, tmp_path / "build")

    assert resolved["remotes"] == {
        "infra": {"url": "https://git.example.com/infra.git", "ref": "main", "commit": infra_head, "dirty": False}
    }
    assert resolved["solution"] == {"ref": "main", "commit": root_head, "dirty": False}
    assert _codes(diagnostics) == []


def test_a_remote_used_by_two_sources_is_recorded_once_with_both_paths_checked(tmp_path: Path):
    _repository(tmp_path / "infra", {"infra/main.tf": "# v1\n", "modules/m.tf": "# m\n", "docs/README.md": "# r\n"})
    root, _ = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra"), ("tf_mods", "infra", "modules")])
    (tmp_path / "infra" / "docs" / "README.md").write_text("# changed\n", encoding="utf-8")

    resolved, diagnostics = _build(root, tmp_path / "build")
    assert list(resolved["remotes"]) == ["infra"]
    assert resolved["remotes"]["infra"]["dirty"] is False
    assert _codes(diagnostics) == []

    (tmp_path / "infra" / "modules" / "m.tf").write_text("# changed\n", encoding="utf-8")
    resolved, diagnostics = _build(root, tmp_path / "build")
    assert resolved["remotes"]["infra"]["dirty"] is True
    assert _codes(diagnostics) == ["remote_checkout_dirty"]


def test_a_strata_clone_records_the_ref_after_the_version_pin(tmp_path: Path):
    upstream = tmp_path / "upstream"
    _repository(upstream, {"infra/main.tf": "# v1\n"})
    _git(upstream, "tag", "v1.0.0")
    _write(upstream, "infra/main.tf", "# v2\n")
    v2 = _commit_all(upstream, "v2")
    _git(upstream, "tag", "v2.0.0")
    url = upstream.as_uri()
    root, _ = _solution(
        tmp_path,
        f"    - name: infra\n      type: git\n      url: {url}\n      reference: v1.0.0\n",
        [("tf_main", "infra", "infra")],
        version="apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  pins:\n    remotes:\n      infra: v2.0.0\n",
    )

    resolved, _ = _build(root, tmp_path / "build")

    assert resolved["remotes"]["infra"] == {"url": url, "ref": "v2.0.0", "commit": v2, "dirty": False}
    # The clone is under `.strata/`, which the solution's own check leaves out.
    assert resolved["solution"]["dirty"] is False


def test_an_external_ref_mode_checkout_is_recorded(tmp_path: Path):
    root, _ = _solution(
        tmp_path,
        "    - name: infra\n      type: git\n      url: https://git.example.com/infra.git\n      reference: main\n"
        "      fetch: external\n",
        [("tf_main", "infra", "infra")],
    )
    head = _repository(layout.remote_checkout_path(root, "infra", "main"), {"infra/main.tf": "# v1\n"})

    resolved, _ = _build(root, tmp_path / "build")

    assert resolved["remotes"]["infra"] == {
        "url": "https://git.example.com/infra.git",
        "ref": "main",
        "commit": head,
        "dirty": False,
    }


def test_a_local_remote_and_the_solution_itself_are_covered_by_the_solution_record(tmp_path: Path):
    root, root_head = _solution(
        tmp_path,
        "    - name: config\n      type: local\n      url: shared\n",
        [("tf_main", "config", "infra"), ("tf_own", None, "own")],
        extra={"shared/infra/main.tf": "# shared\n", "own/main.tf": "# own\n"},
    )

    resolved, _ = _build(root, tmp_path / "build")
    assert resolved["remotes"] == {}
    assert resolved["solution"] == {"ref": "main", "commit": root_head, "dirty": False}

    (root / "shared" / "infra" / "main.tf").write_text("# changed\n", encoding="utf-8")
    resolved, diagnostics = _build(root, tmp_path / "build")
    assert resolved["solution"] == {"ref": "main", "commit": root_head, "dirty": True}
    assert _codes(diagnostics) == ["remote_checkout_dirty"]


def test_the_solution_commit_comes_from_the_ci_variable_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _repository(tmp_path / "infra", {"infra/main.tf": "# v1\n"})
    root, _ = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra")])
    monkeypatch.setenv("BUILD_SOURCEVERSION", "a" * 40)

    resolved, _ = _build(root, tmp_path / "build")

    assert resolved["solution"]["commit"] == "a" * 40


def test_a_build_output_inside_the_solution_does_not_make_it_dirty(tmp_path: Path):
    """`out/` is not in `.gitignore`: the build's own output is left out of the check, not merely ignored."""
    _repository(tmp_path / "infra", {"infra/main.tf": "# v1\n"})
    root, root_head = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra")])

    resolved, diagnostics = _build(root, root / "out" / "app")

    assert resolved["solution"] == {"ref": "main", "commit": root_head, "dirty": False}
    assert _codes(diagnostics) == []


def test_an_edited_document_makes_the_solution_dirty(tmp_path: Path):
    _repository(tmp_path / "infra", {"infra/main.tf": "# v1\n"})
    root, _ = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra")])
    with (root / "environment.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")

    resolved, diagnostics = _build(root, tmp_path / "build")

    assert resolved["solution"]["dirty"] is True
    assert _codes(diagnostics) == ["remote_checkout_dirty"]
    assert "solution repository" in diagnostics.warnings[-1].message


def test_a_path_remote_that_is_not_a_checkout_warns(tmp_path: Path):
    _write(tmp_path / "infra", "infra/main.tf", "# a copy, not a checkout\n")
    root, _ = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra")])

    resolved, diagnostics = _build(root, tmp_path / "build")

    assert resolved["remotes"]["infra"]["commit"] is None
    assert _codes(diagnostics) == ["audit_remote_commit_unavailable"]


def test_a_dry_run_writes_no_record(tmp_path: Path):
    _repository(tmp_path / "infra", {"infra/main.tf": "# v1\n"})
    root, _ = _solution(tmp_path, _PATH_REMOTE, [("tf_main", "infra", "infra")])
    context = open_solution(root)

    build_run(context, "app", tmp_path / "build", dry_run=True)

    assert not (tmp_path / "build" / "resolved.yaml").exists()
