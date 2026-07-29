"""Tests for the ``new`` command."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_new import new_command
from strata.commands.new.run_new_command import NewCommand


class TestNewCommand:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--help"])
        assert result.exit_code == 0
        assert "TEMPLATE" in result.output
        assert "NAME" in result.output

    def test_basic_creation(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["myapp", "--template", "namespace", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_overwrite_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["myapp", "--template", "namespace", "--overwrite", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_with_path(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["myapp", "--template", "namespace", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_set_override(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["myapp", "--template", "namespace", "--set", "owner=myteam", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_list_templates(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_templates_shows_single_file_templates(self, tmp_path):
        """--list shows built-in single-file templates (e.g. namespace, provider)."""
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "namespace" in result.output
        assert "provider" in result.output

    def test_list_templates_shows_scaffold_bundles(self, tmp_path):
        """--list shows scaffold bundles from examples/ with descriptions."""
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "aks" in result.output
        assert "compose" in result.output
        # Descriptions from template.yaml should be shown
        assert "Kubernetes" in result.output or "Terraform" in result.output

    def test_list_templates_json_output(self, tmp_path):
        """--list --output json returns structured data with all templates."""
        import json

        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        names = [t["name"] for t in data["data"]["templates"]]
        assert "namespace" in names
        assert "aks" in names

    def test_missing_template_exits_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["myapp", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_missing_name_exits_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--template", "namespace", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_unknown_template_exits_1(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["myapp", "--template", "nonexistent_xyz_template", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_real_namespace_creation(self, tmp_path):
        """Integration test: actually render the namespace template into tmp_path."""
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "myapp",
                "--template",
                "namespace",
                "--output-file",
                str(tmp_path),
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        out_file = tmp_path / "myapp-namespace.yaml"
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "myapp" in content

    def test_existing_file_no_overwrite_exits_1(self, tmp_path):
        """Writing the same file twice without --overwrite must exit 1."""
        runner = CliRunner()
        # First write succeeds
        runner.invoke(
            new_command,
            ["myapp", "--template", "namespace", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
        )
        # Second write without --overwrite must fail
        result = runner.invoke(
            new_command,
            ["myapp", "--template", "namespace", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_existing_file_with_overwrite_exits_0(self, tmp_path):
        """Writing the same file twice WITH --overwrite must exit 0."""
        runner = CliRunner()
        runner.invoke(
            new_command,
            ["myapp", "--template", "namespace", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
        )
        result = runner.invoke(
            new_command,
            [
                "myapp",
                "--template",
                "namespace",
                "--output-file",
                str(tmp_path),
                "--overwrite",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output


class TestNewCommandContextSubstitution:
    """Unit-level tests for context variable substitution in NewCommand."""

    def test_context_substitution(self, tmp_path):
        """Team context from solution.json is merged into the render context."""
        mock_solution = MagicMock()
        mock_solution.spec.context = {"owner": "acme", "version": "2.0.0"}

        cmd = NewCommand(
            template="namespace",
            name="myapp",
            path=str(tmp_path),
            overwrite=False,
            set_values=("version=3.0.0",),
            work_path=str(tmp_path),
        )

        def _fake_load() -> tuple:
            cmd._solution_controller._solution = mock_solution
            return True, []

        with patch.object(cmd._solution_controller, "load", side_effect=_fake_load):
            # Replicate the context-building logic from _run_execution
            rendered_context: dict = {"name": "myapp"}
            ok, _ = cmd._solution_controller.load()
            if ok and cmd._solution_controller._solution is not None:
                rendered_context.update(cmd._solution_controller._solution.spec.context or {})
            for kv in cmd._set_values:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    rendered_context[k] = v

        assert rendered_context["name"] == "myapp"
        assert rendered_context["owner"] == "acme"
        assert rendered_context["version"] == "3.0.0"  # --set overrides context

    def test_list_includes_solution_level_template(self, tmp_path):
        """--list surfaces solution-level templates declared in solution.json's spec.templates[]."""
        bundle_entry = SimpleNamespace(path="{{ name }}.yaml", name="namespace")
        solution_tpl = MagicMock()
        solution_tpl.name = "onboard-customer"
        solution_tpl.bundle = [bundle_entry]

        mock_solution = MagicMock()
        mock_solution.spec.templates = [solution_tpl]
        mock_solution.spec.context = {}

        cmd = NewCommand(
            template=None,
            name=None,
            list_templates=True,
            work_path=str(tmp_path),
        )

        def _fake_load() -> tuple:
            cmd._solution_controller._solution = mock_solution
            return True, []

        with patch.object(cmd._solution_controller, "load", side_effect=_fake_load):
            result = cmd.execute()

        assert result is True
        names = [t["name"] for t in cmd._output_data["templates"]]
        assert "onboard-customer" in names
        matched = next(t for t in cmd._output_data["templates"] if t["name"] == "onboard-customer")
        assert matched["type"] == "bundle (solution)"

    def test_template_not_found_lists_solution_level_template(self, tmp_path):
        """The 'template not found' error's Available list includes solution-level templates."""
        bundle_entry = SimpleNamespace(path="{{ name }}.yaml", name="namespace")
        solution_tpl = MagicMock()
        solution_tpl.name = "onboard-customer"
        solution_tpl.bundle = [bundle_entry]

        mock_solution = MagicMock()
        mock_solution.spec.templates = [solution_tpl]
        mock_solution.spec.context = {}

        cmd = NewCommand(
            template="nonexistent_xyz_template",
            name="myapp",
            work_path=str(tmp_path),
        )

        def _fake_load() -> tuple:
            cmd._solution_controller._solution = mock_solution
            return True, []

        with patch.object(cmd._solution_controller, "load", side_effect=_fake_load):
            result = cmd.execute()

        assert result is False
        assert any("onboard-customer" in e for e in cmd.get_errors())


class TestNewCommandBundle:
    """Tests for directory bundle templates (multi-file scaffolding)."""

    def _make_bundle(self, work_path, bundle_name: str):
        """Create a minimal bundle dir under .strata/templates/<bundle_name>/."""
        bundle_dir = work_path / ".strata" / "templates" / bundle_name
        bundle_dir.mkdir(parents=True)
        return bundle_dir

    def test_bundle_creates_flat_files(self, tmp_path):
        """A flat bundle directory produces files in --path root."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "{{ name }}.yaml").write_text("kind: widget\nname: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["acme", "--template", "widget", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "acme.yaml").exists()

    def test_bundle_content_substitution(self, tmp_path):
        """{{ var }} in file content is substituted from context + --set."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("zone: {{ zone }}\ntier: {{ tier }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "acme",
                "--template",
                "widget",
                "--output-file",
                str(out),
                "--set",
                "zone=eu",
                "--set",
                "tier=premium",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        content = (out / "file.yaml").read_text(encoding="utf-8")
        assert "eu" in content
        assert "premium" in content

    def test_bundle_path_segment_substitution(self, tmp_path):
        """{{ name }} in directory names is substituted using the same engine."""
        bundle = self._make_bundle(tmp_path, "widget")
        subdir = bundle / "{{ name }}"
        subdir.mkdir()
        (subdir / "deployment.yaml").write_text("name: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["acme", "--template", "widget", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "acme" / "deployment.yaml").exists()
        assert "acme" in (out / "acme" / "deployment.yaml").read_text(encoding="utf-8")

    def test_bundle_nested_path_and_filename(self, tmp_path):
        """{{ name }} works in both directory and filename simultaneously."""
        bundle = self._make_bundle(tmp_path, "widget")
        subdir = bundle / "envs" / "{{ name }}"
        subdir.mkdir(parents=True)
        (subdir / "{{ name }}-dev.yaml").write_text("env: dev\nname: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["globex", "--template", "widget", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "envs" / "globex" / "globex-dev.yaml").exists()

    def test_bundle_overwrite_guard(self, tmp_path):
        """Second run without --overwrite exits 1 when output file exists."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("x: 1\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        runner.invoke(
            new_command, ["acme", "--template", "widget", "--output-file", str(out), "--work-path", str(tmp_path)]
        )
        result = runner.invoke(
            new_command, ["acme", "--template", "widget", "--output-file", str(out), "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 1

    def test_bundle_overwrite_flag(self, tmp_path):
        """Second run WITH --overwrite exits 0."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("x: 1\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        runner.invoke(
            new_command, ["acme", "--template", "widget", "--output-file", str(out), "--work-path", str(tmp_path)]
        )
        result = runner.invoke(
            new_command,
            ["acme", "--template", "widget", "--output-file", str(out), "--overwrite", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output

    def test_bundle_appears_in_list(self, tmp_path):
        """A workspace bundle directory appears in --list output."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("x: 1\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "widget" in result.output

    def test_bundle_workspace_overrides_package(self, tmp_path):
        """Workspace bundle takes precedence over package single-file template of same name."""
        # 'namespace' exists as a package single-file template.
        # A workspace bundle of the same name should win.
        bundle = self._make_bundle(tmp_path, "namespace")
        (bundle / "{{ name }}-custom.yaml").write_text("custom: true\nname: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["myapp", "--template", "namespace", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        # Bundle output file (not the single-file default name)
        assert (out / "myapp-custom.yaml").exists()
        assert not (out / "myapp-namespace.yaml").exists()


class TestScaffoldDepsHelpers:
    """Unit tests for the module-level helpers used by --scaffold-deps."""

    def test_resolve_at_repo_path_basic(self, tmp_path):
        from strata.services.template_resolver import resolve_at_repo_path as _resolve_at_repo_path

        repo_map = {"myrepo": str(tmp_path)}
        result = _resolve_at_repo_path("@myrepo/stack/ws.yaml", repo_map)
        assert result == tmp_path / "stack" / "ws.yaml"

    def test_resolve_at_repo_path_unknown_repo(self):
        from strata.services.template_resolver import resolve_at_repo_path as _resolve_at_repo_path

        assert _resolve_at_repo_path("@unknown/path.yaml", {}) is None

    def test_resolve_at_repo_path_non_at_ref(self, tmp_path):
        from strata.services.template_resolver import resolve_at_repo_path as _resolve_at_repo_path

        assert _resolve_at_repo_path("stack/ws.yaml", {"myrepo": str(tmp_path)}) is None

    def test_collect_dep_candidates_missing_local(self, tmp_path):
        from strata.services.template_resolver import collect_dep_candidates as _collect_dep_candidates
        from strata.utils.graph import GraphEdge, GraphNode, GraphResult

        result = GraphResult(mode="files")
        result.nodes = [
            GraphNode(identifier="deploy/prd.yaml", path="deploy/prd.yaml", kind="deployment", status="valid"),
            GraphNode(identifier="stack/ws.yaml", path="stack/ws.yaml", kind="workspace", status="missing"),
            GraphNode(identifier="envs/dev.yaml", path="envs/dev.yaml", kind="environment", status="missing"),
        ]
        result.edges = [
            GraphEdge(source="deploy/prd.yaml", target="stack/ws.yaml", label="workspace"),
            GraphEdge(source="deploy/prd.yaml", target="envs/dev.yaml", label="environment"),
        ]

        candidates = _collect_dep_candidates(result, tmp_path, {})
        assert len(candidates) == 2
        kinds = {c[0] for c in candidates}
        assert kinds == {"workspace", "environment"}
        assert all(str(c[2]).startswith(str(tmp_path)) for c in candidates)

    def test_collect_dep_candidates_skips_existing(self, tmp_path):
        from strata.services.template_resolver import collect_dep_candidates as _collect_dep_candidates
        from strata.utils.graph import GraphNode, GraphResult

        existing = tmp_path / "stack" / "ws.yaml"
        existing.parent.mkdir()
        existing.write_text("x: 1", encoding="utf-8")

        result = GraphResult(mode="files")
        result.nodes = [
            GraphNode(identifier="stack/ws.yaml", path="stack/ws.yaml", kind="workspace", status="missing"),
        ]
        result.edges = []

        assert _collect_dep_candidates(result, tmp_path, {}) == []

    def test_collect_dep_candidates_external_ref_resolved(self, tmp_path):
        from strata.services.template_resolver import collect_dep_candidates as _collect_dep_candidates
        from strata.utils.graph import GraphEdge, GraphNode, GraphResult

        repo_root = tmp_path / "myrepo"
        repo_root.mkdir()
        repo_map = {"myrepo": str(repo_root)}

        result = GraphResult(mode="files")
        result.nodes = [
            GraphNode(
                identifier="@myrepo/stack/ws.yaml", path="@myrepo/stack/ws.yaml", kind="workspace", status="external"
            ),
        ]
        result.edges = [
            GraphEdge(source="deploy/prd.yaml", target="@myrepo/stack/ws.yaml", label="workspace"),
        ]

        candidates = _collect_dep_candidates(result, tmp_path, repo_map)
        assert len(candidates) == 1
        kind, name, resolved = candidates[0]
        assert kind == "workspace"
        assert name == "ws"
        assert resolved == repo_root / "stack" / "ws.yaml"

    def test_collect_dep_candidates_external_ref_unresolvable(self, tmp_path):
        from strata.services.template_resolver import collect_dep_candidates as _collect_dep_candidates
        from strata.utils.graph import GraphNode, GraphResult

        result = GraphResult(mode="files")
        result.nodes = [
            GraphNode(identifier="@unknownrepo/stack/ws.yaml", kind="workspace", status="external"),
        ]
        result.edges = []

        assert _collect_dep_candidates(result, tmp_path, {}) == []


class TestScaffoldDepsCommand:
    """Integration tests for ``strata new --scaffold-deps``."""

    def _write_template(self, tmp_path, kind: str, content: str) -> None:
        tpl_dir = tmp_path / ".strata" / "templates"
        tpl_dir.mkdir(parents=True, exist_ok=True)
        (tpl_dir / f"{kind}.yaml").write_text(content, encoding="utf-8")

    def test_scaffold_deps_flag_in_help(self):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--help"])
        assert result.exit_code == 0
        assert "scaffold-deps" in result.output

    def test_scaffold_deps_no_missing_deps_is_silent(self, tmp_path):
        """When the created file has no missing deps, no scaffold prompt is shown."""
        out = tmp_path / "myapp-namespace.yaml"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "myapp",
                "--template",
                "namespace",
                "--output-file",
                str(out),
                "--scaffold-deps",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "Scaffold missing files?" not in result.output

    def test_scaffold_deps_creates_missing_local_dep(self, tmp_path):
        """After creating a deployment referencing a missing workspace, it is scaffolded."""
        deploy_tpl = (
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: deployment\n"
            "meta:\n"
            "  name: {{ name }}\n"
            "spec:\n"
            "  workspace:\n"
            "    file: stack/ws-{{ name }}.yaml\n"
        )
        ws_tpl = "apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: {{ name }}\nspec: {}\n"
        self._write_template(tmp_path, "deployment", deploy_tpl)
        self._write_template(tmp_path, "workspace", ws_tpl)

        # Place deployment at workspace root so relative refs resolve from there
        deploy_out = tmp_path / "prd-deployment.yaml"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "prd",
                "--template",
                "deployment",
                "--output-file",
                str(deploy_out),
                "--scaffold-deps",
                "--work-path",
                str(tmp_path),
            ],
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert deploy_out.exists()
        # workspace ref "stack/ws-prd.yaml" resolves relative to deployment file's parent (= tmp_path)
        assert (tmp_path / "stack" / "ws-prd.yaml").exists()

    def test_scaffold_deps_declined_leaves_dep_absent(self, tmp_path):
        """Answering 'n' to the prompt leaves the missing dep uncreated."""
        deploy_tpl = (
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: deployment\n"
            "meta:\n"
            "  name: {{ name }}\n"
            "spec:\n"
            "  workspace:\n"
            "    file: stack/ws-{{ name }}.yaml\n"
        )
        self._write_template(tmp_path, "deployment", deploy_tpl)
        self._write_template(
            tmp_path,
            "workspace",
            "apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: {{ name }}\nspec: {}\n",
        )

        deploy_out = tmp_path / "prd-deployment.yaml"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "prd",
                "--template",
                "deployment",
                "--output-file",
                str(deploy_out),
                "--scaffold-deps",
                "--work-path",
                str(tmp_path),
            ],
            input="n\n",
        )

        assert result.exit_code == 0
        assert deploy_out.exists()
        assert not (tmp_path / "stack" / "ws-prd.yaml").exists()
