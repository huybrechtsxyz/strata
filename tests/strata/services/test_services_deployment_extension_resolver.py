"""Tests for DeploymentExtensionResolver (ADR 0039 — Phase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from strata.services.deployment_extension_resolver import DeploymentExtensionResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER = """\
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
"""


def _write(path: Path, spec: Dict[str, Any], meta: Dict[str, Any] | None = None) -> Path:
    """Write a minimal deployment YAML to *path* and return it."""
    import yaml

    doc: Dict[str, Any] = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "deployment",
        "meta": {"name": "test", **(meta or {})},
        "spec": spec,
    }
    path.write_text(yaml.dump(doc, default_flow_style=False), encoding="utf-8")
    return path


def _resolver(tmp_path: Path, repo_map: Dict[str, str] | None = None) -> DeploymentExtensionResolver:
    return DeploymentExtensionResolver(work_path=tmp_path, repo_map=repo_map or {})


# ---------------------------------------------------------------------------
# needs_resolution
# ---------------------------------------------------------------------------


class TestNeedsResolution:
    def test_returns_true_when_extends_present(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "child.yaml", {"extends": "@base/base.yaml", "workspace": {"name": "ws"}})
        resolver = _resolver(tmp_path)
        assert resolver.needs_resolution(f) is True

    def test_returns_false_without_extends(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "leaf.yaml", {"workspace": {"name": "ws"}})
        resolver = _resolver(tmp_path)
        assert resolver.needs_resolution(f) is False

    def test_returns_false_when_spec_missing(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\nmeta:\n  name: x\n", encoding="utf-8")
        resolver = _resolver(tmp_path)
        assert resolver.needs_resolution(f) is False


# ---------------------------------------------------------------------------
# resolve — no extends
# ---------------------------------------------------------------------------


class TestResolveNoExtends:
    def test_returns_raw_content_unchanged(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "leaf.yaml", {"workspace": {"name": "myws"}})
        resolver = _resolver(tmp_path)
        result = resolver.resolve(f)
        assert result["spec"]["workspace"] == {"name": "myws"}

    def test_strips_partial_flag(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "p.yaml", {"partial": True, "workspace": {"name": "ws"}})
        resolver = _resolver(tmp_path)
        result = resolver.resolve(f)
        assert "partial" not in result["spec"]

    def test_strips_extends_none(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "p.yaml", {"extends": None, "workspace": {"name": "ws"}})
        resolver = _resolver(tmp_path)
        result = resolver.resolve(f)
        assert "extends" not in result["spec"]


# ---------------------------------------------------------------------------
# resolve — relative extends path resolves against the workspace root
# ---------------------------------------------------------------------------


class TestResolveRelativePathIsWorkspaceRootRelative:
    def test_relative_extends_from_nested_child_resolves_against_work_path(self, tmp_path: Path) -> None:
        """Regression test: a relative spec.extends reference must resolve
        against work_path, not the child file's own directory — ADR-0039:
        'spec.extends accepts ... the same resolution rules as all other
        cross-file references in strata', matching BaseService._resolve_file_path().
        """
        (tmp_path / "templates").mkdir()
        (tmp_path / "deploy").mkdir()
        _write(
            tmp_path / "templates" / "ring-base.yaml",
            {"partial": True, "workspace": {"name": "base-ws"}},
        )
        child = _write(
            tmp_path / "deploy" / "child.yaml",
            {"extends": "templates/ring-base.yaml", "workspace": {"name": "child-ws"}},
        )
        result = _resolver(tmp_path).resolve(child)

        assert result["spec"]["workspace"] == {"name": "child-ws"}


# ---------------------------------------------------------------------------
# resolve — single-level extends
# ---------------------------------------------------------------------------


class TestResolveSingleLevel:
    def test_top_level_field_child_wins(self, tmp_path: Path) -> None:
        base = _write(tmp_path / "base.yaml", {"workspace": {"name": "base-ws"}, "partial": True})
        child_spec = {
            "extends": str(base),
            "workspace": {"name": "child-ws"},
        }
        child = _write(tmp_path / "child.yaml", child_spec)
        result = _resolver(tmp_path).resolve(child)
        assert result["spec"]["workspace"] == {"name": "child-ws"}

    def test_base_field_preserved_when_child_omits_it(self, tmp_path: Path) -> None:
        base = _write(
            tmp_path / "base.yaml",
            {"partial": True, "workspace": {"name": "base-ws"}, "properties": {"env": "prod"}},
        )
        child = _write(tmp_path / "child.yaml", {"extends": str(base), "workspace": {"name": "child-ws"}})
        result = _resolver(tmp_path).resolve(child)
        assert result["spec"]["properties"] == {"env": "prod"}
        assert result["spec"]["workspace"] == {"name": "child-ws"}

    def test_partial_stripped_from_result(self, tmp_path: Path) -> None:
        base = _write(tmp_path / "base.yaml", {"partial": True, "workspace": {"name": "ws"}})
        child = _write(tmp_path / "child.yaml", {"extends": str(base)})
        result = _resolver(tmp_path).resolve(child)
        assert "partial" not in result["spec"]

    def test_extends_stripped_from_result(self, tmp_path: Path) -> None:
        base = _write(tmp_path / "base.yaml", {"partial": True, "workspace": {"name": "ws"}})
        child = _write(tmp_path / "child.yaml", {"extends": str(base)})
        result = _resolver(tmp_path).resolve(child)
        assert "extends" not in result["spec"]

    def test_stages_new_child_stage_appended(self, tmp_path: Path) -> None:
        base = _write(
            tmp_path / "base.yaml",
            {"partial": True, "stages": [{"name": "networking", "provisioner": "platform_iac"}]},
        )
        child = _write(
            tmp_path / "child.yaml",
            {
                "extends": str(base),
                "stages": [{"name": "compute", "provisioner": "platform_iac"}],
            },
        )
        result = _resolver(tmp_path).resolve(child)
        names = [s["name"] for s in result["spec"]["stages"]]
        assert names == ["networking", "compute"]

    def test_stages_child_overrides_matching_name(self, tmp_path: Path) -> None:
        base = _write(
            tmp_path / "base.yaml",
            {
                "partial": True,
                "stages": [{"name": "compute", "provisioner": "platform_iac", "scope": "all"}],
            },
        )
        child = _write(
            tmp_path / "child.yaml",
            {
                "extends": str(base),
                "stages": [{"name": "compute", "scope": "region-a"}],
            },
        )
        result = _resolver(tmp_path).resolve(child)
        stages = result["spec"]["stages"]
        assert len(stages) == 1
        assert stages[0]["scope"] == "region-a"
        assert stages[0]["provisioner"] == "platform_iac"  # preserved from base

    def test_environments_appended(self, tmp_path: Path) -> None:
        base = _write(
            tmp_path / "base.yaml",
            {"partial": True, "environments": ["envs/base.yaml"]},
        )
        child = _write(
            tmp_path / "child.yaml",
            {"extends": str(base), "environments": ["envs/prod.yaml"]},
        )
        result = _resolver(tmp_path).resolve(child)
        assert result["spec"]["environments"] == ["envs/base.yaml", "envs/prod.yaml"]

    def test_meta_child_wins_per_key(self, tmp_path: Path) -> None:
        base = _write(
            tmp_path / "base.yaml",
            {"partial": True},
            meta={"name": "base", "labels": {"version": "1.0"}},
        )
        child = _write(
            tmp_path / "child.yaml",
            {"extends": str(base)},
            meta={"name": "child", "labels": {"version": "2.0"}},
        )
        result = _resolver(tmp_path).resolve(child)
        assert result["meta"]["name"] == "child"
        assert result["meta"]["labels"]["version"] == "2.0"


# ---------------------------------------------------------------------------
# resolve — multi-level chain
# ---------------------------------------------------------------------------


class TestResolveMultiLevel:
    def test_three_level_chain(self, tmp_path: Path) -> None:
        grandparent = _write(
            tmp_path / "grandparent.yaml",
            {
                "partial": True,
                "stages": [{"name": "networking", "provisioner": "platform_iac"}],
                "properties": {"org": "corp"},
            },
        )
        parent = _write(
            tmp_path / "parent.yaml",
            {
                "partial": True,
                "extends": str(grandparent),
                "stages": [{"name": "compute", "provisioner": "platform_iac"}],
            },
        )
        child = _write(
            tmp_path / "child.yaml",
            {
                "extends": str(parent),
                "workspace": {"name": "ws"},
                "stages": [{"name": "compute", "scope": "region-b"}],
            },
        )
        result = _resolver(tmp_path).resolve(child)
        spec = result["spec"]

        # Properties flowed from grandparent
        assert spec["properties"] == {"org": "corp"}
        # Stage names include both
        names = [s["name"] for s in spec["stages"]]
        assert "networking" in names
        assert "compute" in names
        # Child's override on compute wins
        compute = next(s for s in spec["stages"] if s["name"] == "compute")
        assert compute["scope"] == "region-b"
        assert compute["provisioner"] == "platform_iac"  # inherited from grandparent→parent
        # Workspace set by child
        assert spec["workspace"] == {"name": "ws"}

    def test_deep_environments_accumulate(self, tmp_path: Path) -> None:
        gp = _write(tmp_path / "gp.yaml", {"partial": True, "environments": ["a.yaml"]})
        parent = _write(
            tmp_path / "parent.yaml",
            {"partial": True, "extends": str(gp), "environments": ["b.yaml"]},
        )
        child = _write(tmp_path / "child.yaml", {"extends": str(parent), "environments": ["c.yaml"]})
        result = _resolver(tmp_path).resolve(child)
        assert result["spec"]["environments"] == ["a.yaml", "b.yaml", "c.yaml"]


# ---------------------------------------------------------------------------
# resolve — error cases
# ---------------------------------------------------------------------------


class TestResolveErrors:
    def test_self_reference_raises_value_error(self, tmp_path: Path) -> None:
        f = tmp_path / "self.yaml"
        _write(f, {"extends": str(f)})
        with pytest.raises(ValueError, match="Circular"):
            _resolver(tmp_path).resolve(f)

    def test_mutual_cycle_raises_value_error(self, tmp_path: Path) -> None:
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        _write(a, {"extends": str(b)})
        _write(b, {"extends": str(a)})
        with pytest.raises(ValueError, match="Circular"):
            _resolver(tmp_path).resolve(a)

    def test_missing_base_file_raises_file_not_found(self, tmp_path: Path) -> None:
        child = _write(tmp_path / "child.yaml", {"extends": str(tmp_path / "nonexistent.yaml")})
        with pytest.raises(FileNotFoundError):
            _resolver(tmp_path).resolve(child)

    def test_unknown_repo_ref_raises_value_error(self, tmp_path: Path) -> None:
        child = _write(tmp_path / "child.yaml", {"extends": "@unknown-repo/base.yaml"})
        with pytest.raises(ValueError, match="Cannot resolve extends reference"):
            _resolver(tmp_path).resolve(child)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text(": : invalid: yaml: [[[", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            _resolver(tmp_path).resolve(f)


# ---------------------------------------------------------------------------
# resolve — @repo cross-repo reference
# ---------------------------------------------------------------------------


class TestResolveRepoRef:
    def test_repo_ref_resolves_via_repo_map(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "shared-repo"
        base_dir.mkdir()
        base = _write(base_dir / "base.yaml", {"partial": True, "workspace": {"name": "shared-ws"}})

        child = _write(tmp_path / "child.yaml", {"extends": "@shared/base.yaml"})
        resolver = DeploymentExtensionResolver(
            work_path=tmp_path,
            repo_map={"shared": str(base_dir)},
        )
        result = resolver.resolve(child)
        assert result["spec"]["workspace"] == {"name": "shared-ws"}


# ---------------------------------------------------------------------------
# _merge_stages (unit)
# ---------------------------------------------------------------------------


class TestMergeStages:
    def test_empty_base_returns_child(self) -> None:
        child = [{"name": "a", "provisioner": "terraform"}]
        result = DeploymentExtensionResolver._merge_stages([], child)
        assert result == [{"name": "a", "provisioner": "terraform"}]

    def test_empty_child_returns_base(self) -> None:
        base = [{"name": "a", "provisioner": "terraform"}]
        result = DeploymentExtensionResolver._merge_stages(base, [])
        assert result == [{"name": "a", "provisioner": "terraform"}]

    def test_override_existing_stage(self) -> None:
        base = [{"name": "a", "provisioner": "terraform", "scope": "all"}]
        child = [{"name": "a", "scope": "eu-west"}]
        result = DeploymentExtensionResolver._merge_stages(base, child)
        assert len(result) == 1
        assert result[0]["scope"] == "eu-west"
        assert result[0]["provisioner"] == "terraform"  # preserved from base

    def test_append_new_stage(self) -> None:
        base = [{"name": "a", "provisioner": "terraform"}]
        child = [{"name": "b", "provisioner": "ansible"}]
        result = DeploymentExtensionResolver._merge_stages(base, child)
        assert [s["name"] for s in result] == ["a", "b"]

    def test_mixed_override_and_append(self) -> None:
        base = [
            {"name": "networking", "provisioner": "terraform", "scope": "all"},
            {"name": "compute", "provisioner": "terraform"},
        ]
        child = [
            {"name": "networking", "scope": "region-a"},  # override
            {"name": "configure", "provisioner": "ansible"},  # new
        ]
        result = DeploymentExtensionResolver._merge_stages(base, child)
        names = [s["name"] for s in result]
        assert names == ["networking", "compute", "configure"]
        networking = result[0]
        assert networking["scope"] == "region-a"
        assert networking["provisioner"] == "terraform"

    def test_stage_without_name_appended(self) -> None:
        base = [{"name": "a"}]
        child = [{"provisioner": "terraform"}]  # no name
        result = DeploymentExtensionResolver._merge_stages(base, child)
        assert len(result) == 2

    def test_preserves_base_stage_order(self) -> None:
        base = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        child = [{"name": "b", "scope": "x"}]
        result = DeploymentExtensionResolver._merge_stages(base, child)
        assert [s["name"] for s in result] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _append_environments (unit)
# ---------------------------------------------------------------------------


class TestAppendEnvironments:
    def test_base_plus_child(self) -> None:
        result = DeploymentExtensionResolver._append_environments(["a.yaml"], ["b.yaml"])
        assert result == ["a.yaml", "b.yaml"]

    def test_empty_base(self) -> None:
        result = DeploymentExtensionResolver._append_environments([], ["b.yaml"])
        assert result == ["b.yaml"]

    def test_empty_child(self) -> None:
        result = DeploymentExtensionResolver._append_environments(["a.yaml"], [])
        assert result == ["a.yaml"]

    def test_both_empty(self) -> None:
        result = DeploymentExtensionResolver._append_environments([], [])
        assert result == []

    def test_multiple_items(self) -> None:
        result = DeploymentExtensionResolver._append_environments(
            ["base.yaml", "common.yaml"],
            ["prod.yaml", "secrets.yaml"],
        )
        assert result == ["base.yaml", "common.yaml", "prod.yaml", "secrets.yaml"]
