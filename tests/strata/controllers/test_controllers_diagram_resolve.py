#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_resolve.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : strata:// URI resolution tests for strata CLI (ADR-0034).
===============================================================================
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli_diagram import diagram_group
from strata.controllers.diagram_resolve_controller import DiagramResolveController

AZURE_AKS_PATH = Path(__file__).resolve().parents[3] / "config" / "azure-aks"

WORKSPACE_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: sample_ws
spec:
  resources:
    - name: app_server
      description: the app
      modules:
        - name: api_gateway
    - name: db_server
  namespaces:
    - name: core
"""

ENVIRONMENT_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: env_prd
spec:
  secrets:
    - key: DB_PASSWORD
      source: bitwarden
"""


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "stack").mkdir()
    (tmp_path / "stack" / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
    (tmp_path / "env.yaml").write_text(ENVIRONMENT_YAML, encoding="utf-8")
    return tmp_path


class TestFileUris:
    def test_resolves_to_the_file_with_no_line(self, workspace):
        """A file reference points at the document, not a position inside it."""
        result = DiagramResolveController(workspace).resolve("strata://file/stack/workspace.yaml")
        assert result["file"] == "stack/workspace.yaml"
        assert result["line"] is None

    def test_missing_file_is_reported(self, workspace):
        controller = DiagramResolveController(workspace)
        assert controller.resolve("strata://file/nope.yaml") is None
        assert "does not exist" in controller.get_errors()[0]


class TestDocumentUris:
    def test_resolves_to_the_meta_name_line(self, workspace):
        result = DiagramResolveController(workspace).resolve("strata://workspace/sample_ws")
        assert result["file"] == "stack/workspace.yaml"
        assert result["line"] == 4

    def test_unknown_document_is_reported(self, workspace):
        controller = DiagramResolveController(workspace)
        assert controller.resolve("strata://workspace/nope") is None
        assert "names no workspace called 'nope'" in controller.get_errors()[0]

    def test_kind_must_match_too(self, workspace):
        """The same name under a different kind is a different object."""
        controller = DiagramResolveController(workspace)
        assert controller.resolve("strata://deployment/sample_ws") is None
        assert controller.has_errors()


class TestChildUris:
    def test_resource(self, workspace):
        result = DiagramResolveController(workspace).resolve("strata://workspace/sample_ws/resource/app_server")
        assert (result["file"], result["line"]) == ("stack/workspace.yaml", 7)

    def test_second_resource(self, workspace):
        result = DiagramResolveController(workspace).resolve("strata://workspace/sample_ws/resource/db_server")
        assert result["line"] == 11

    def test_namespace(self, workspace):
        result = DiagramResolveController(workspace).resolve("strata://workspace/sample_ws/namespace/core")
        assert result["line"] == 13

    def test_nested_collection_resolves_without_a_path_table(self, workspace):
        """A module lives under spec.resources[].modules, not at the top level."""
        result = DiagramResolveController(workspace).resolve("strata://workspace/sample_ws/module/api_gateway")
        assert result["line"] == 10

    def test_key_is_accepted_as_an_identity_field(self, workspace):
        """Environment secrets are keyed rather than named."""
        result = DiagramResolveController(workspace).resolve("strata://environment/env_prd/secret/DB_PASSWORD")
        assert (result["file"], result["line"]) == ("env.yaml", 7)

    def test_missing_child_names_where_it_looked(self, workspace):
        controller = DiagramResolveController(workspace)
        assert controller.resolve("strata://workspace/sample_ws/resource/ghost") is None
        error = controller.get_errors()[0]
        assert "has no resource named 'ghost'" in error
        assert "resources" in error

    def test_gate_is_accepted_as_an_identity_field(self, tmp_path):
        """PromotionGateResultModel identifies itself by 'gate', not 'name'/'key'."""
        (tmp_path / ".strata" / "promotions" / "records").mkdir(parents=True)
        (tmp_path / ".strata" / "promotions" / "records" / "prom.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: promotion-record\n"
            "meta:\n"
            "  name: prom\n"
            "spec:\n"
            "  gates:\n"
            "    - gate: require_quorum\n"
            "      ring: prd\n"
            "      passed: false\n",
            encoding="utf-8",
        )
        result = DiagramResolveController(tmp_path).resolve("strata://promotion-record/prom/gate/require_quorum")
        assert result is not None
        assert result["file"] == ".strata/promotions/records/prom.yaml"


class TestHiddenDirectoryScanning:
    def test_dot_strata_is_scanned(self, tmp_path):
        """.strata/ holds real kind/meta.name documents (promotion records, etc.) worth resolving."""
        (tmp_path / ".strata" / "promotions" / "records").mkdir(parents=True)
        (tmp_path / ".strata" / "promotions" / "records" / "prom.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: promotion-record\nmeta:\n  name: prom\nspec: {}\n",
            encoding="utf-8",
        )
        result = DiagramResolveController(tmp_path).resolve("strata://promotion-record/prom")
        assert result is not None
        assert result["file"] == ".strata/promotions/records/prom.yaml"

    def test_other_dot_directories_are_still_skipped(self, tmp_path):
        """Only '.strata/' is scanned — '.git' and similar stay excluded."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "decoy.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: decoy\nspec: {}\n",
            encoding="utf-8",
        )
        controller = DiagramResolveController(tmp_path)
        assert controller.resolve("strata://workspace/decoy") is None


class TestStructuralNotPositional:
    def test_inserting_lines_above_the_target_does_not_break_the_uri(self, workspace):
        """The whole point of encoding no line number in the URI."""
        uri = "strata://workspace/sample_ws/resource/app_server"
        before = DiagramResolveController(workspace).resolve(uri)

        path = workspace / "stack" / "workspace.yaml"
        path.write_text(
            WORKSPACE_YAML.replace("spec:\n", "spec:\n  networks:\n    - name: vnet\n"),
            encoding="utf-8",
        )
        after = DiagramResolveController(workspace).resolve(uri)

        assert after is not None
        assert after["line"] == before["line"] + 2


class TestMalformedUris:
    def test_bad_scheme_is_reported(self, workspace):
        controller = DiagramResolveController(workspace)
        assert controller.resolve("https://example.com") is None
        assert "not a strata URI" in controller.get_errors()[0]

    def test_unparseable_workspace_files_are_skipped_not_fatal(self, workspace):
        (workspace / "broken.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        result = DiagramResolveController(workspace).resolve("strata://workspace/sample_ws")
        assert result is not None


class TestResolveCommand:
    def test_console_output_is_path_and_line(self):
        result = CliRunner().invoke(
            diagram_group,
            [
                "resolve",
                "strata://workspace/azure_aks_platform/resource/postgres",
                "--work-path",
                str(AZURE_AKS_PATH),
            ],
        )
        assert result.exit_code == 0
        assert "stack/azure-ws-platform.yaml:45" in result.output

    def test_console_output_omits_line_for_a_file(self):
        result = CliRunner().invoke(
            diagram_group,
            ["resolve", "strata://file/deploy/azure-aks-deploy-prd.yaml", "--work-path", str(AZURE_AKS_PATH)],
        )
        assert result.exit_code == 0
        assert "deploy/azure-aks-deploy-prd.yaml" in result.output
        assert "yaml:" not in result.output

    def test_unresolvable_uri_exits_non_zero(self):
        result = CliRunner().invoke(
            diagram_group,
            ["resolve", "strata://workspace/azure_aks_platform/resource/ghost", "--work-path", str(AZURE_AKS_PATH)],
        )
        assert result.exit_code == 1

    def test_uri_argument_is_required(self):
        assert CliRunner().invoke(diagram_group, ["resolve"]).exit_code == 2

    def test_round_trips_a_uri_emitted_by_a_diagram(self):
        """The URIs `diagram show` emits must be the URIs `diagram resolve` accepts."""
        rendered = CliRunner().invoke(
            diagram_group,
            [
                "show",
                "-f",
                "topology",
                "--entry",
                "stack/azure-ws-platform.yaml",
                "--no-validate",
                "--work-path",
                str(AZURE_AKS_PATH),
            ],
        )
        assert rendered.exit_code == 0
        uris = [
            line.split('"')[1]
            for line in rendered.output.splitlines()
            if line.strip().startswith("click") and '"' in line
        ]
        assert uris, "expected the topology diagram to emit click directives"
        for uri in uris:
            resolved = CliRunner().invoke(diagram_group, ["resolve", uri, "--work-path", str(AZURE_AKS_PATH)])
            assert resolved.exit_code == 0, f"{uri}: {resolved.output}"
