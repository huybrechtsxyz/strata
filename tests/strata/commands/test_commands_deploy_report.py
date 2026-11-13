#!/usr/bin/env python3
"""End to end for `strata deploy report` (docs/design/deployment-documentation.md, section 1).

A real `deploy run` of a Terraform step and a Helm step, with every tool call faked and a fake key-value
`output_store`, then `deploy report`: outputs, sensitive names, the secrets each step used with their locations,
and that no secret value appears anywhere: not in the report, not in the store.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.solution_context import open_solution
from strata.integrations import registry as registry_module
from strata.integrations.azure_keyvault_resolver import AzureKeyVaultResolver
from strata.integrations.capabilities import KeyValueStoreIntegration
from strata.integrations.helm import HelmIntegration
from strata.integrations.resolved_context import KeyValueEntry
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import CommandResult

pytestmark = pytest.mark.slow


_KV_VALUE = "kv-Secret-Value-123"
_ENV_VALUE = "env-Secret-Value-456"
_CONSTANT_VALUE = "c0nstant-Secret-Value"
_OUTPUT_SECRET = "s3cr3t-Output-789"
_ALL_SECRET_VALUES = (_KV_VALUE, _ENV_VALUE, _CONSTANT_VALUE, _OUTPUT_SECRET)

_H = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


class _Backend(KeyValueStoreIntegration):
    TYPE = "fake-output-store"
    CAPABILITIES = frozenset({"key_value"})
    TRANSPORTS = frozenset({"sdk"})
    data: dict[str, bytes] = {}

    def get(self, key: str) -> KeyValueEntry | None:
        return KeyValueEntry(value=self.data[key], version="1") if key in self.data else None

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        self.data[key] = value
        return "1"


@dataclass
class _EntryPoint:
    name: str
    value: str
    _target: Any = field(default=_Backend)

    def load(self) -> Any:
        return self._target


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(
    tmp_path: Path, *, output_store: bool = True, git_sink: bool = False, documentation: str | None = None
) -> Path:
    """`apply_infra` (Terraform: declares variables SQL_PWD and LEGACY, two outputs, one sensitive) and
    `deploy_apps` (Helm: a module service reads ${secret:API_KEY}). UNUSED is declared and used by nothing."""
    root = tmp_path / "sln"
    remotes = (
        "  remotes:\n    - name: audit-repo\n      type: git\n      url: https://example.invalid/audit.git\n"
        "      reference: main\n"
        if git_sink
        else "  {}\n"
    )
    _write(root, "strata.yaml", _H.format(kind="solution", name="s") + remotes)
    if git_sink:
        _write(
            root,
            "configuration.yaml",
            _H.format(kind="configuration", name="cfg") + "  audit:\n    sinks:\n      - name: audit-repo\n"
            "        git:\n          remote: audit-repo\n          branch: main\n          path: audit\n",
        )
    _write(
        root,
        "infra/main.tf",
        'variable "SQL_PWD" {}\nvariable "LEGACY" {}\n'
        'output "fqdn" {\n  value = "x"\n  description = "SQL server FQDN"\n}\n'
        'output "conn" {\n  value = "x"\n  sensitive = true\n  description = "Also in kv-web as sql-conn"\n}\n',
    )
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\n")
    _write(root, "provider.yaml", _H.format(kind="provider", name="p1") + "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        _H.format(kind="resource", name="r1")
        + "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    a: b\n",
    )
    _write(
        root,
        "module.yaml",
        _H.format(kind="module", name="authentik")
        + "  source:\n    source_path: charts/authentik\n  type: helm\n  default_labels:\n    app: authentik\n"
        "  services:\n    - name: server\n      environment:\n        - key: API_KEY\n          value: ${secret:API_KEY}\n",
    )
    _write(
        root,
        "namespace.yaml",
        _H.format(kind="namespace", name="apps")
        + "  default_labels:\n    app: apps\n  modules:\n    - name: auth\n      module: authentik\n",
    )
    store = "  output_store: fake-output-store\n" if output_store else ""
    _write(
        root,
        "workspace.yaml",
        _H.format(kind="workspace", name="main") + store + "  providers:\n    - p1\n  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "    - name: helm_main\n      tool: helm\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      scope: infra\n"
        "    - name: deploy_apps\n      provisioner: helm_main\n      namespaces:\n        - apps\n"
        "      scope: apps\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environments/prd.yaml",
        _H.format(kind="environment", name="prd") + "  secrets:\n"
        "    - key: SQL_PWD\n      store: environment\n      value: SQL_PWD_VAR\n      description: SQL admin login\n"
        "    - key: API_KEY\n      store: azure-keyvault\n      value: api-key\n"
        f"    - key: LEGACY\n      store: constant\n      value: {_CONSTANT_VALUE}\n"
        "    - key: UNUSED\n      store: environment\n      value: UNUSED_VAR\n",
    )
    _write(
        root,
        "app.yaml",
        _H.format(kind="deployment", name="app")
        + "  workspace: main\n  environments:\n    - prd\n"
        + (f"  documentation:\n{documentation}" if documentation is not None else ""),
    )
    return root


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    _Backend.data = {}
    monkeypatch.setattr(
        registry_module, "entry_points", lambda group: [_EntryPoint(name="fake-output-store", value="x:Y")]
    )
    monkeypatch.setattr(HelmIntegration, "READ_VERSION", False)
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://kv-prd-weu.vault.azure.net/")
    monkeypatch.setenv("SQL_PWD_VAR", _ENV_VALUE)
    monkeypatch.setenv("UNUSED_VAR", "unused-Value-000")
    monkeypatch.setattr(AzureKeyVaultResolver, "resolve", lambda self, key: _KV_VALUE)

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        if args[0] == "terraform" and args[1] == "output":
            payload = {
                "fqdn": {"value": "sql.example.net", "type": "string", "sensitive": False},
                "conn": {"value": _OUTPUT_SECRET, "type": "string", "sensitive": True},
            }
            return CommandResult(returncode=0, stdout=json.dumps(payload), stderr="")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")


def _deploy(tmp_path: Path, root: Path) -> None:
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    build_path = tmp_path / "build"
    build_run(context, "app", build_path)
    diagnostics = deploy_run(open_solution(root), "app", build_path, force=True)
    assert diagnostics.ok, diagnostics.messages()


def _report(root: Path, *args: str):
    return CliRunner().invoke(cli, ["deploy", "report", "app", *args, "--path", str(root)])


def test_report_lists_outputs_and_the_secrets_each_step_used_by_location_never_by_value(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)

    result = _report(root, "-o", "json")

    assert result.exit_code == EXIT_SUCCESS, result.output
    payload = json.loads(result.output)
    data = payload["data"]
    steps = {step["step"]: step for step in data["steps"]}
    assert steps["apply_infra"]["secret_keys"] == ["LEGACY", "SQL_PWD"]  # TF_VAR for declared variables only
    assert steps["deploy_apps"]["secret_keys"] == ["API_KEY"]  # ${secret:} in the module's values
    assert steps["deploy_apps"]["status"] == "not_recorded"  # Helm has no outputs
    assert steps["deploy_apps"]["secrets"] == "recorded"
    assert [(row["name"], row["sensitive"], row["value"]) for row in steps["apply_infra"]["outputs"]] == [
        ("conn", True, None),
        ("fqdn", False, "sql.example.net"),
    ]

    secrets = {row["key"]: row for row in data["secrets"]}
    assert set(secrets) == {"API_KEY", "LEGACY", "SQL_PWD"}  # UNUSED is declared but used by nothing
    assert all(row["value"] == "********" for row in secrets.values())
    assert secrets["API_KEY"]["location"] == {"vault": "https://kv-prd-weu.vault.azure.net/", "name": "api-key"}
    assert secrets["API_KEY"]["where"] == "kv-prd-weu.vault.azure.net / api-key (latest)"
    assert secrets["API_KEY"]["used_by"] == ["deploy_apps"]
    assert secrets["SQL_PWD"]["where"] == "env var SQL_PWD_VAR (set on the deploy runner)"
    assert secrets["SQL_PWD"]["description"] == "SQL admin login"
    assert secrets["LEGACY"]["location"] == {}
    assert secrets["LEGACY"]["where"] == "held in source (environments/prd.yaml), move to a vault"
    assert "secret_held_in_source" in result.output

    stored = json.dumps({key: value.decode() for key, value in _Backend.data.items()})
    for value in _ALL_SECRET_VALUES:
        assert value not in result.output, value
        assert value not in stored, value


def test_table_masks_every_secret_and_sensitive_output(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)

    result = _report(root)

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "kv-prd-weu.vault.azure.net / api-key (latest)" in result.output
    assert "conn" in result.output and "sql.example.net" in result.output
    assert result.output.count("********") >= 4  # three secrets and one sensitive output
    for value in _ALL_SECRET_VALUES:
        assert value not in result.output, value


def test_step_filter_lists_only_that_steps_secrets(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)

    data = json.loads(_report(root, "--step", "deploy_apps", "-o", "json").output)["data"]

    assert [step["step"] for step in data["steps"]] == ["deploy_apps"]
    assert [row["key"] for row in data["secrets"]] == ["API_KEY"]


def test_an_unknown_step_is_a_usage_error(tmp_path):
    result = _report(_solution(tmp_path), "--step", "nope")
    assert result.exit_code == EXIT_USAGE
    assert "No execution step 'nope'" in result.output


def test_without_an_output_store_the_report_fails_validation(tmp_path):
    result = _report(_solution(tmp_path, output_store=False))
    assert result.exit_code == EXIT_VALIDATION


def test_a_step_recorded_by_an_earlier_strata_says_secrets_not_recorded(tmp_path):
    """A report record without a `secrets` field (release 2) never reads as 'no secrets'."""
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    key = "strata/deployment-report/app/apply_infra"
    record = json.loads(_Backend.data[key])
    del record["secrets"]
    _Backend.data[key] = json.dumps(record).encode()

    result = _report(root, "-o", "json")

    data = json.loads(result.output)["data"]
    steps = {step["step"]: step for step in data["steps"]}
    assert steps["apply_infra"]["secrets"] == "not_recorded"
    assert "report_secrets_not_recorded" in result.output
    assert {row["key"] for row in data["secrets"]} == {"API_KEY"}


# ---------------------------------------------------------------------------
# Release 4: the Markdown document, --template, --output-path, spec.documentation
# ---------------------------------------------------------------------------


def test_markdown_goes_to_stdout_alone_with_every_secret_masked(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)

    result = _report(root, "--format", "markdown")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert result.stdout.startswith("# app — deployment handover\n")
    assert "| conn | ******** | Also in kv-web as sql-conn |" in result.stdout
    assert "| API_KEY | ******** | azure-keyvault | kv-prd-weu.vault.azure.net / api-key (latest) |" in result.stdout
    assert "deploy report" in result.stderr  # the run's report is kept off stdout
    for value in _ALL_SECRET_VALUES:
        assert value not in result.output, value


def test_output_path_writes_the_document_and_the_envelope_names_it(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    target = tmp_path / "out" / "handover.md"

    result = _report(root, "--output-path", str(target), "-o", "json")

    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert data == {"deployment": "app", "written_to": str(target), "format": "markdown"}
    document = target.read_text(encoding="utf-8")
    assert "## Secrets" in document
    for value in _ALL_SECRET_VALUES:
        assert value not in document, value


def test_a_custom_template_receives_the_json_data(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    template = tmp_path / "t.md.j2"
    template.write_text(
        "{% for s in secrets %}{{ s.key }}={{ s.where }}\n{% endfor %}{{ steps | length }} steps\n", encoding="utf-8"
    )

    result = _report(root, "--template", str(template))

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert result.stdout == (
        "API_KEY=kv-prd-weu.vault.azure.net / api-key (latest)\n"
        "LEGACY=held in source (environments/prd.yaml), move to a vault\n"
        "SQL_PWD=env var SQL_PWD_VAR (set on the deploy runner)\n"
        "2 steps\n"
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{{ secret_values }}", "'secret_values' is not in the report data"),
        ("{{ steps.__class__.__mro__ }}", "does not render"),
        # Not Jinja2 errors: Python's own, raised by the expression.
        ("{{ steps | length / 0 }}", "ZeroDivisionError"),
        ("{{ deployment + 1 }}", "TypeError"),
    ],
)
def test_a_template_that_cannot_render_is_a_usage_error(tmp_path, source, message):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    template = tmp_path / "t.md.j2"
    template.write_text(source, encoding="utf-8")

    result = _report(root, "--template", str(template))

    assert result.exit_code == EXIT_USAGE
    assert message in result.output


def test_table_format_with_output_path_is_a_usage_error(tmp_path):
    result = _report(_solution(tmp_path), "--format", "table", "--output-path", str(tmp_path / "x.md"))
    assert result.exit_code == EXIT_USAGE
    assert "use --format markdown" in result.output


def _validate_codes(root: Path) -> set[str]:
    context = open_solution(root)
    context.resolve()
    return {item.code for item in context.diagnostics.errors}


def test_validate_rejects_documentation_without_output_store_or_git_sink(tmp_path):
    codes = _validate_codes(_solution(tmp_path, output_store=False, documentation="    sink: audit\n"))
    assert {"documentation_needs_output_store", "documentation_needs_git_audit_sink"} <= codes


def test_validate_checks_the_documentation_template(tmp_path):
    root = _solution(tmp_path, git_sink=True, documentation="    template: docs/handover.md.j2\n")
    assert "documentation_template_missing" in _validate_codes(root)

    _write(root, "docs/handover.md.j2", "{{ nope }}")
    assert "documentation_template_invalid" in _validate_codes(root)

    _write(root, "docs/handover.md.j2", "# {{ deployment }}\n")
    assert not _validate_codes(root)


def test_a_successful_deploy_pushes_the_handover_document_with_the_audit_record(tmp_path, monkeypatch):
    import strata.controllers.audit_run as audit_run_module
    from strata.controllers.audit_push import PushResult

    root = _solution(tmp_path, git_sink=True, documentation="    sink: audit\n")
    _deploy(tmp_path, root)
    pushed: dict[str, Path] = {}

    def _fake_push(root_arg, sink, solution, files, relative_path, actor, **kwargs):
        pushed.update(files)
        return PushResult(True)

    monkeypatch.setattr(audit_run_module, "push_audit_files", _fake_push)

    diagnostics = audit_run_module.finalize_and_distribute_deploy_audit(
        open_solution(root).require_valid(),
        "app",
        tmp_path / "build",
        execution_id="11111111-1111-1111-1111-111111111111",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        run_diagnostics=Diagnostics(),
    )

    assert not diagnostics.warnings, diagnostics.messages()
    assert set(pushed) == {"_manifest.json", "_metrics.json", "_handover.md"}
    document = pushed["_handover.md"].read_text(encoding="utf-8")
    assert "audit record `11111111-1111-1111-1111-111111111111`" in document
    assert "kv-prd-weu.vault.azure.net / api-key (latest)" in document
    for value in _ALL_SECRET_VALUES:
        assert value not in document, value


@pytest.mark.parametrize(
    "source",
    [
        "{{ steps[99].step }}",  # passes validate, fails to render (a Jinja2 error)
        "{{ steps | length / 0 }}",  # a Python error, not a Jinja2 one
    ],
)
def test_a_failed_render_is_a_warning_and_the_audit_record_is_still_pushed(tmp_path, monkeypatch, source):
    import strata.controllers.audit_run as audit_run_module
    from strata.controllers.audit_push import PushResult

    root = _solution(tmp_path, git_sink=True, documentation="    template: docs/handover.md.j2\n")
    _write(root, "docs/handover.md.j2", source)
    _deploy(tmp_path, root)
    pushed: dict[str, Path] = {}
    monkeypatch.setattr(audit_run_module, "push_audit_files", lambda *a, **k: pushed.update(a[3]) or PushResult(True))

    diagnostics = audit_run_module.finalize_and_distribute_deploy_audit(
        open_solution(root).require_valid(),
        "app",
        tmp_path / "build",
        execution_id="11111111-1111-1111-1111-111111111111",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        run_diagnostics=Diagnostics(),
    )

    assert diagnostics.ok
    assert [warning.code for warning in diagnostics.warnings] == ["documentation_render_failed"]
    assert set(pushed) == {"_manifest.json", "_metrics.json"}


def test_a_successful_deploy_records_its_audit_summary_for_the_report(tmp_path, monkeypatch):
    """`deploy report` says who deployed, when and from which commit without reading the git audit sink."""
    import strata.controllers.audit_run as audit_run_module

    monkeypatch.setenv("STRATA_COMMIT_SHA", "4602813" + "0" * 33)
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    audit_run_module.finalize_and_distribute_deploy_audit(
        open_solution(root).require_valid(),
        "app",
        tmp_path / "build",
        execution_id="22222222-2222-2222-2222-222222222222",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        run_diagnostics=Diagnostics(),
    )

    manifest = json.loads(next((root / ".strata").rglob("_manifest.json")).read_text(encoding="utf-8"))
    assert manifest["commit_sha"] == "4602813" + "0" * 33

    audit = json.loads(_report(root, "-o", "json").output)["data"]["audit"]
    assert audit["execution_id"] == "22222222-2222-2222-2222-222222222222"
    assert audit["commit_sha"] == "4602813" + "0" * 33
    assert audit["status"] == "success" and audit["deployed_by"]

    document = _report(root, "--format", "markdown").stdout
    assert "from commit `4602813`" in document
    assert "audit record `22222222-2222-2222-2222-222222222222`" in document


def test_without_a_recorded_deploy_the_report_has_no_audit(tmp_path):
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    assert json.loads(_report(root, "-o", "json").output)["data"]["audit"] is None


def test_build_estimates_the_report_record_of_a_helm_step_too(tmp_path):
    root = _solution(tmp_path)
    steps: list[str] = []

    build_run(open_solution(root).require_valid(), "app", tmp_path / "build", on_step=steps.append)

    assert any("step 'deploy_apps': 0 output(s) declared, 1 secret(s) used" in step for step in steps), steps


def test_a_deploy_without_a_commit_sha_still_renders_the_document(tmp_path, monkeypatch):
    """Outside a git checkout and without a CI commit variable the summary has `commit_sha: null`; the built-in
    template, under StrictUndefined, must still render."""
    import strata.controllers.audit_run as audit_run_module

    monkeypatch.setattr(audit_run_module, "built_commit_sha", lambda root: None)
    root = _solution(tmp_path)
    _deploy(tmp_path, root)
    audit_run_module.finalize_and_distribute_deploy_audit(
        open_solution(root).require_valid(),
        "app",
        tmp_path / "build",
        execution_id="33333333-3333-3333-3333-333333333333",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        run_diagnostics=Diagnostics(),
    )

    result = _report(root, "--format", "markdown")
    assert result.exit_code == 0, result.output
    assert "from commit" not in result.stdout
    assert "audit record `33333333-3333-3333-3333-333333333333`" in result.stdout
    assert json.loads(_report(root, "-o", "json").output)["data"]["audit"]["commit_sha"] is None


def test_the_handover_document_describes_the_pinned_deploy(tmp_path, monkeypatch):
    """`deploy run --pin v1`: the document pushed with the audit record lists what was deployed, the pinned
    version's images, not whatever `spec.version` says on disk."""
    import strata.controllers.audit_run as audit_run_module
    import strata.controllers.deployment_report_view as report_view_module
    from strata.controllers.audit_push import PushResult

    root = _solution(tmp_path, git_sink=True, documentation="    sink: audit\n")
    _write(root, "version.yaml", _H.format(kind="version", name="v1") + "  pins:\n    artifacts:\n      x: '1.0.0'\n")
    _deploy(tmp_path, root)
    monkeypatch.setattr(audit_run_module, "push_audit_files", lambda *a, **k: PushResult(True))
    seen: list[tuple[str | None, bool]] = []
    real_effective_images = report_view_module.effective_images

    def _spy(context, deployment, *, pinned_by_flag=False):
        seen.append((deployment.spec.version, pinned_by_flag))
        return real_effective_images(context, deployment, pinned_by_flag=pinned_by_flag)

    monkeypatch.setattr(report_view_module, "effective_images", _spy)

    diagnostics = audit_run_module.finalize_and_distribute_deploy_audit(
        open_solution(root).require_valid(),
        "app",
        tmp_path / "build",
        execution_id="44444444-4444-4444-4444-444444444444",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        run_diagnostics=Diagnostics(),
        pin="v1",
    )

    assert not diagnostics.warnings, diagnostics.messages()
    assert seen == [("v1", True)]
