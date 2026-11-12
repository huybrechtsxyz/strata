#!/usr/bin/env python3
"""`deploy_run(destroy=True)` (docs/design/deploy-destroy.md).

Same approach as `test_deploy_controller.py`: a real solution is built with `build_run()`, then deployed against
a stubbed `run_command`, so no `terraform`/`helm`/`docker` binary is needed. Each stubbed call records its
argument vector, working directory, environment and timeout. One test at the end uses a real `terraform`.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from strata.controllers import deploy_controller as deploy_module
from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.solution_context import open_solution
from strata.integrations.errors import IntegrationError
from strata.integrations.gitops import ArgoCDIntegration
from strata.integrations.terraform import TerraformIntegration
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import SystemError, UsageError
from strata.utils.transport import CommandResult

MANIFEST = "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec: {}\n"
STORE_KEY = "strata/deployment-outputs/app"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def _solution(
    tmp_path: Path,
    *,
    provisioners: str,
    execution: str,
    workspace_extra: str = "",
    environment_spec: str = " {}",
    manifest: str = MANIFEST,
    files: dict[str, str] | None = None,
) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", manifest)
    for relative, content in {
        "infra_a/main.tf": "# root module\n",
        "infra_b/main.tf": "# root module\n",
        **(files or {}),
    }.items():
        _write(root, relative, content)
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  type: local\n  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  provider_type: local\n  resource_type: server\n  category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        f"{workspace_extra}"
        "  providers:\n    - p1\n"
        f"  provisioners:\n{provisioners}"
        f"  execution:\n{execution}"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:{environment_spec}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


_TF_PROVISIONERS = (
    "    - name: tf_infra\n      tool: terraform\n      source:\n        source_path: infra_a\n"
    "    - name: tf_apps\n      tool: terraform\n      source:\n        source_path: infra_b\n"
)
_TF_APPS_NEEDING_AN_OUTPUT = (
    "    - name: tf_infra\n      tool: terraform\n      source:\n        source_path: infra_a\n"
    "    - name: tf_apps\n      tool: terraform\n      source:\n        source_path: infra_b\n"
    "      backend:\n        type: local\n        configuration:\n          host: ${output:apply_infra.vm_ip}\n"
)
_TF_EXECUTION = (
    "    - name: apply_infra\n      provisioner: tf_infra\n      resources:\n        - r1\n      scope: infra\n"
    "    - name: apply_apps\n      provisioner: tf_apps\n      resources:\n        - r1\n"
    "      depends_on:\n        - apply_infra\n      scope: apps\n"
)


def _tf_two(tmp_path: Path, *, apps_need_an_output: bool = False, **kwargs: Any) -> Path:
    """Two dependent Terraform steps in two directories, so a recorded call says which step made it."""
    return _solution(
        tmp_path,
        provisioners=_TF_APPS_NEEDING_AN_OUTPUT if apps_need_an_output else _TF_PROVISIONERS,
        execution=_TF_EXECUTION,
        **kwargs,
    )


def _helm_solution(tmp_path: Path, **kwargs: Any) -> Path:
    root = _solution(
        tmp_path,
        provisioners=(
            "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra_a\n"
            "    - name: helm_main\n      tool: helm\n      source:\n        source_path: infra_a\n"
        ),
        execution=(
            "    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n      scope: infra\n"
            "    - name: deploy_apps\n      provisioner: helm_main\n      namespaces:\n        - apps\n"
            "      scope: apps\n"
        ),
        # These tests count the deploy's own helm calls; the build's `helm template` for the SBOM is off.
        workspace_extra="  sbom:\n    render_charts: false\n  namespaces:\n    - apps\n"
        + kwargs.pop("workspace_extra", ""),
        files={"charts/authentik/Chart.yaml": "name: authentik\n"},
        **kwargs,
    )
    _write(
        root,
        "module.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: authentik\nspec:\n"
        "  source:\n    source_path: charts/authentik\n  type: helm\n"
        "  default_labels:\n    app: authentik\n  services:\n    - name: server\n",
    )
    _write(
        root,
        "namespace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
        "  default_labels:\n    app: apps\n  modules:\n    - name: auth\n      module: authentik\n",
    )
    return root


def _compose_solution(tmp_path: Path) -> Path:
    root = _solution(
        tmp_path,
        provisioners=(
            "    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra_a\n"
            "    - name: compose_main\n      tool: compose\n      source:\n        source_path: infra_a\n"
        ),
        execution=(
            "    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n      scope: infra\n"
            "    - name: deploy_apps\n      provisioner: compose_main\n      namespaces:\n        - apps\n"
            "      scope: apps\n"
        ),
        workspace_extra="  namespaces:\n    - apps\n",
        files={"services/portainer/.keep": ""},
    )
    _write(
        root,
        "module.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: portainer\nspec:\n"
        "  source:\n    source_path: services/portainer\n  type: compose\n"
        "  default_labels:\n    app: portainer\n"
        "  services:\n    - name: portainer\n      image: portainer/portainer-ce\n",
    )
    _write(
        root,
        "namespace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
        "  default_labels:\n    app: apps\n  modules:\n    - name: mgmt\n      module: portainer\n",
    )
    return root


# ---------------------------------------------------------------------------
# Stubbed tools
# ---------------------------------------------------------------------------


@dataclass
class _Call:
    args: list[str]
    cwd: Path | None
    env: dict[str, str] | None
    timeout: int
    tfvars_present: bool


@dataclass
class _Tools:
    calls: list[_Call] = field(default_factory=list)
    responders: list[Any] = field(default_factory=list)

    def terraform(self) -> list[tuple[str, str]]:
        """`(step directory, verb)` for every lifecycle call Terraform received, in order."""
        verbs = {"init", "validate", "plan", "apply", "destroy", "output"}
        return [
            (call.cwd.name if call.cwd else "?", call.args[1])
            for call in self.calls
            if call.args[0] == "terraform" and call.args[1] in verbs
        ]

    def of(self, tool: str, verb: str | None = None) -> list[_Call]:
        """Calls to `tool`, leaving out the version probes made while checking it is installed."""
        return [
            c
            for c in self.calls
            if c.args[0] == tool and c.args[1] not in ("version", "--version") and (verb is None or c.args[1] == verb)
        ]


@pytest.fixture
def tools(monkeypatch):
    recorder = _Tools()

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        call = _Call(
            list(args),
            cwd,
            dict(env) if env is not None else None,
            timeout,
            tfvars_present=cwd is not None and (cwd / "strata.auto.tfvars.json").exists(),
        )
        recorder.calls.append(call)
        for responder in recorder.responders:
            result = responder(call)
            if result is not None:
                return result
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return recorder


def _register_output_store(monkeypatch):
    """A fake key-value backend registered as a real integration type; its data is class-level so it survives
    the fresh instances the registry returns. `get_calls` counts reads."""
    from dataclasses import dataclass as _dataclass

    from strata.integrations import registry as registry_module
    from strata.integrations.capabilities import NEW_KEY_VERSION, KeyValueStoreIntegration
    from strata.integrations.errors import KeyValueConflictError
    from strata.integrations.resolved_context import KeyValueEntry

    class _Backend(KeyValueStoreIntegration):
        TYPE = "fake-output-store"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})
        shared_data: dict[str, tuple[bytes, str]] = {}
        version_counter = 0
        get_calls = 0

        def get(self, key: str) -> KeyValueEntry | None:
            type(self).get_calls += 1
            if key not in type(self).shared_data:
                return None
            value, version = type(self).shared_data[key]
            return KeyValueEntry(value=value, version=version)

        def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
            existing = type(self).shared_data.get(key)
            if if_version == NEW_KEY_VERSION and existing is not None:
                raise KeyValueConflictError("already exists")
            if if_version is not None and if_version != NEW_KEY_VERSION:
                if (existing[1] if existing is not None else None) != if_version:
                    raise KeyValueConflictError("modified concurrently")
            type(self).version_counter += 1
            type(self).shared_data[key] = (value, str(type(self).version_counter))
            return str(type(self).version_counter)

    _Backend.shared_data = {}
    _Backend.version_counter = 0
    _Backend.get_calls = 0

    @_dataclass
    class _EntryPoint:
        name: str
        value: str

        def load(self) -> Any:
            return _Backend

    monkeypatch.setattr(
        registry_module, "entry_points", lambda group: [_EntryPoint(name="fake-output-store", value="x:Y")]
    )
    return _Backend


_STORE = "  output_store: fake-output-store\n"


def _seed(backend, outputs: dict[str, dict[str, str]], *, collected_at: str = "2999-01-01T00:00:00+00:00") -> None:
    record = {step: {"collected_at": collected_at, "values": values} for step, values in outputs.items()}
    backend.shared_data[STORE_KEY] = (json.dumps(record).encode("utf-8"), "1")


def _record(backend) -> dict:
    return json.loads(backend.shared_data[STORE_KEY][0])


def _built(tmp_path: Path, root: Path) -> Path:
    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    return build_path


def _destroy(root: Path, build_path: Path, **kwargs: Any):
    steps: list[str] = []
    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, on_step=steps.append, **kwargs)
    return diagnostics, steps


def _codes(diagnostics: Diagnostics, kind: str) -> list[str | None]:
    return [item.code for item in getattr(diagnostics, kind)]


# ---------------------------------------------------------------------------
# Order, selection, and what a destroy does not evaluate
# ---------------------------------------------------------------------------


def test_destroy_runs_the_dependent_step_before_the_step_it_depends_on(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, steps = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert tools.terraform() == [
        ("infra_b", "init"),
        ("infra_b", "destroy"),
        ("infra_a", "init"),
        ("infra_a", "destroy"),
    ]
    assert [s for s in steps if s.startswith("destroyed step")] == [
        "destroyed step 'apply_apps' via TerraformIntegration",
        "destroyed step 'apply_infra' via TerraformIntegration",
    ]


def test_destroy_stage_filter_selects_the_one_step(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, stages=("apply_apps",))

    assert diagnostics.ok, diagnostics.messages()
    assert tools.terraform() == [("infra_b", "init"), ("infra_b", "destroy")]
    assert diagnostics.warnings == []


def test_destroy_scope_filter_selects_the_steps_with_that_scope(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, scopes=("apps",))

    assert diagnostics.ok, diagnostics.messages()
    assert tools.terraform() == [("infra_b", "init"), ("infra_b", "destroy")]


def test_destroy_repeated_stages_destroy_in_reverse_order(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, stages=("apply_infra", "apply_apps"))

    assert diagnostics.ok, diagnostics.messages()
    assert [call for call in tools.terraform() if call[1] == "destroy"] == [
        ("infra_b", "destroy"),
        ("infra_a", "destroy"),
    ]


def test_destroy_does_not_evaluate_the_image_tag_policy(tmp_path: Path, tools, monkeypatch):
    root = _tf_two(tmp_path, workspace_extra="  image_tag_policy: {}\n")
    build_path = _built(tmp_path, root)
    evaluated: list[str] = []

    def _spy(*args, **kwargs):
        evaluated.append("evaluated")
        return Diagnostics()

    monkeypatch.setattr(deploy_module, "check_image_tag_policy", _spy)

    destroy, _ = _destroy(root, build_path, force=True)
    assert destroy.ok, destroy.messages()
    assert evaluated == []

    deploy_run(_context(root), "app", build_path, force=True)
    assert evaluated == ["evaluated"]  # the same solution does evaluate it on a deploy


def test_destroy_does_not_evaluate_the_security_gates(tmp_path: Path, tools, monkeypatch):
    root = _tf_two(
        tmp_path,
        workspace_extra=(
            "  release_gate_policy:\n    integration: gate-ci\n    repository: org/infra\n"
            f"    enforcement: deny\n    commit: {'a' * 40}\n"
        ),
        environment_spec="\n  variables:\n    - key: GATE_TOKEN\n      store: environment\n      value: TEST_GATE_TOKEN",
    )
    _write(
        root,
        "integration.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: gate-ci\nspec:\n"
        "  type: fake-gate\n  capabilities: [release_gate]\n"
        "  authentication:\n    method: api_key\n    api_key:\n      api_key: GATE_TOKEN\n",
    )
    monkeypatch.setenv("TEST_GATE_TOKEN", "token-1234567890")
    build_path = _built(tmp_path, root)
    evaluated: list[str] = []

    def _spy(*args, **kwargs):
        evaluated.append("evaluated")
        return SimpleNamespace(diagnostics=Diagnostics(), policy_results=lambda workspace: [])

    monkeypatch.setattr(deploy_module, "evaluate_security_gates", _spy)

    destroy, _ = _destroy(root, build_path, force=True)
    assert destroy.ok, destroy.messages()
    assert evaluated == []

    deploy_run(_context(root), "app", build_path, force=True)
    assert evaluated == ["evaluated"]


@pytest.mark.parametrize("flag", [{"stop_after": "setup"}, {"use_saved_plan": True}])
def test_destroy_cannot_be_combined_with_the_other_modes(tmp_path: Path, tools, flag):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    with pytest.raises(UsageError, match="cannot be combined"):
        deploy_run(_context(root), "app", build_path, destroy=True, force=True, **flag)

    assert tools.terraform() == []


def test_destroy_needs_force_dry_run_or_smoke_test(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    with pytest.raises(UsageError, match="force=True to destroy, dry_run=True to preview or smoke_test=True"):
        deploy_run(_context(root), "app", build_path, destroy=True)

    assert tools.terraform() == []


@pytest.mark.parametrize("filters", [{"stages": ("no_such_stage",)}, {"scopes": ("no_such_scope",)}])
def test_destroy_that_selects_no_step_fails_instead_of_reporting_success(tmp_path: Path, tools, filters):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, **filters)

    assert not diagnostics.ok
    assert diagnostics.errors[0].code == "destroy_no_steps"
    assert tools.terraform() == []


# ---------------------------------------------------------------------------
# Pre-flight: unsupported tools, dependents left behind
# ---------------------------------------------------------------------------


def _with_a_script_step(tmp_path: Path) -> Path:
    return _solution(
        tmp_path,
        provisioners=(
            "    - name: tf_infra\n      tool: terraform\n      source:\n        source_path: infra_a\n"
            "    - name: script_main\n      tool: script\n      source:\n        source_path: scripts\n"
            "      script:\n        file: deploy.py\n"
        ),
        execution=(
            "    - name: apply_infra\n      provisioner: tf_infra\n      resources:\n        - r1\n"
            "    - name: run_script\n      provisioner: script_main\n      resources:\n        - r1\n"
            "      depends_on:\n        - apply_infra\n"
        ),
        files={"scripts/deploy.py": "print('hi')\n"},
    )


def test_a_step_that_cannot_destroy_stops_the_run_before_anything_is_destroyed(tmp_path: Path, tools):
    root = _with_a_script_step(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert _codes(diagnostics, "errors") == ["destroy_unsupported"]
    assert "run_script" in diagnostics.errors[0].message
    assert "--stage" in diagnostics.errors[0].message
    assert tools.terraform() == []


def test_a_step_that_cannot_destroy_is_only_a_warning_under_dry_run(tmp_path: Path, tools):
    root = _with_a_script_step(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings") == ["destroy_unsupported"]
    assert tools.terraform() == [("infra_a", "init"), ("infra_a", "plan")]  # the script step is left alone


def test_a_step_that_cannot_destroy_can_be_left_out(tmp_path: Path, tools):
    root = _with_a_script_step(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, stages=("apply_infra",))

    assert diagnostics.ok, diagnostics.messages()
    assert tools.terraform() == [("infra_a", "init"), ("infra_a", "destroy")]


def test_destroying_an_upstream_step_alone_warns_about_the_dependents_it_leaves(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, scopes=("infra",))

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings") == ["destroy_leaves_dependents"]
    assert "'apply_apps'" in diagnostics.warnings[0].message
    assert tools.terraform() == [("infra_a", "init"), ("infra_a", "destroy")]  # a warning, never a stop


def test_the_leaves_dependents_warning_also_shows_under_dry_run(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, dry_run=True, stages=("apply_infra",))

    assert _codes(diagnostics, "warnings") == ["destroy_leaves_dependents"]


def test_no_dependents_warning_when_the_dependent_is_part_of_the_run(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.warnings == []


# ---------------------------------------------------------------------------
# Pre-flight: ${output:} references (D6)
# ---------------------------------------------------------------------------


def _chained(tmp_path: Path, **kwargs: Any) -> tuple[Path, Path]:
    root = _tf_two(tmp_path, apps_need_an_output=True, **kwargs)
    return root, _built(tmp_path, root)


def test_a_referenced_output_is_read_from_the_output_store_with_a_warning(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings") == ["destroy_uses_outputs"]
    assert "'apply_infra'" in diagnostics.warnings[0].message
    apps_init = next(c for c in tools.of("terraform", "init") if c.cwd is not None and c.cwd.name == "infra_b")
    assert "host=10.0.0.5" in apps_init.args


def test_a_referenced_output_with_no_output_store_stops_the_run_before_anything_is_destroyed(tmp_path: Path, tools):
    root, build_path = _chained(tmp_path)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert _codes(diagnostics, "errors") == ["destroy_output_unavailable"]
    message = diagnostics.errors[0].message
    assert "${output:apply_infra.vm_ip}" in message
    assert "no output_store" in message
    assert "deploy run" in message
    assert tools.terraform() == []


def test_a_referenced_output_with_no_record_is_unavailable(tmp_path: Path, tools, monkeypatch):
    _register_output_store(monkeypatch)
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert _codes(diagnostics, "errors") == ["destroy_output_unavailable"]
    assert "none is recorded" in diagnostics.errors[0].message
    assert tools.terraform() == []


def test_a_referenced_output_missing_from_its_record_is_unavailable(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"something_else": "x"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert _codes(diagnostics, "errors") == ["destroy_output_unavailable"]
    assert "has no output 'vm_ip'" in diagnostics.errors[0].message
    assert "sensitive" in diagnostics.errors[0].message


def test_a_stale_record_is_unavailable_until_max_age_is_raised(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}}, collected_at="2020-01-01T00:00:00+00:00")
    root, build_path = _chained(tmp_path, workspace_extra=_STORE + "  output_store_max_age: 60\n")

    stale, _ = _destroy(root, build_path, force=True)

    assert _codes(stale, "errors") == ["destroy_output_unavailable"]
    assert "--output-max-age" in stale.errors[0].message
    assert tools.terraform() == []

    accepted, _ = _destroy(root, build_path, force=True, output_max_age=10**10)
    assert accepted.ok, accepted.messages()


def test_a_dry_run_cannot_preview_a_destroy_whose_outputs_are_unavailable(tmp_path: Path, tools):
    root, build_path = _chained(tmp_path)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert _codes(diagnostics, "errors") == ["destroy_output_unavailable"]


def test_a_stale_record_does_not_block_a_step_that_references_no_output(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}}, collected_at="2020-01-01T00:00:00+00:00")
    root, build_path = _chained(tmp_path, workspace_extra=_STORE + "  output_store_max_age: 60\n")

    diagnostics, _ = _destroy(root, build_path, dry_run=True, stages=("apply_infra",))

    assert diagnostics.ok, diagnostics.messages()
    assert backend.get_calls == 0  # nothing referenced an output, so the store was never read


def test_a_container_step_never_reads_the_output_store(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}}, collected_at="2020-01-01T00:00:00+00:00")
    root = _helm_solution(tmp_path, workspace_extra=_STORE + "  output_store_max_age: 60\n")
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert backend.get_calls == 0


# ---------------------------------------------------------------------------
# Smoke test: what would be destroyed, with no tool and no output store contact
# ---------------------------------------------------------------------------


def test_a_smoke_test_reports_each_step_last_first_and_calls_no_tool(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, steps = _destroy(root, build_path, smoke_test=True)

    assert diagnostics.ok, diagnostics.messages()
    assert [s for s in steps if s.startswith("would ")] == [
        "would destroy step 'apply_apps' via TerraformIntegration",
        "would destroy step 'apply_infra' via TerraformIntegration",
    ]
    assert tools.calls == []  # not even a version probe


def test_a_smoke_test_covers_a_container_step_without_calling_its_tool(tmp_path: Path, tools):
    root = _helm_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, steps = _destroy(root, build_path, smoke_test=True)

    assert diagnostics.ok, diagnostics.messages()
    assert "would destroy step 'deploy_apps' via HelmIntegration" in steps
    assert tools.of("helm") == []


def test_a_smoke_test_still_refuses_a_step_that_cannot_destroy(tmp_path: Path, tools):
    root = _with_a_script_step(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, smoke_test=True)

    assert _codes(diagnostics, "errors") == ["destroy_unsupported"]  # a real destroy would stop here too
    assert tools.terraform() == []


def test_a_smoke_test_warns_about_dependents_it_would_leave(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, smoke_test=True, stages=("apply_infra",))

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings") == ["destroy_leaves_dependents"]


def test_a_smoke_test_that_selects_no_step_fails(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, smoke_test=True, stages=("no_such_stage",))

    assert _codes(diagnostics, "errors") == ["destroy_no_steps"]


def test_a_smoke_test_does_not_read_or_clear_the_output_store(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}, "apply_apps": {"k": "v"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)
    before = backend.shared_data[STORE_KEY]

    diagnostics, _ = _destroy(root, build_path, smoke_test=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings") == ["destroy_uses_outputs"]
    assert "not read in a smoke test" in diagnostics.warnings[0].message
    assert backend.get_calls == 0
    assert backend.shared_data[STORE_KEY] == before


def test_a_smoke_test_still_finds_a_reference_no_output_store_could_serve(tmp_path: Path, tools):
    root, build_path = _chained(tmp_path)  # references an output, configures no output_store

    diagnostics, _ = _destroy(root, build_path, smoke_test=True)

    assert _codes(diagnostics, "errors") == ["destroy_output_unavailable"]
    assert "no output_store" in diagnostics.errors[0].message


# ---------------------------------------------------------------------------
# Terraform-style steps
# ---------------------------------------------------------------------------


def test_force_destroys_with_auto_approve(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, stages=("apply_infra",))

    assert diagnostics.ok, diagnostics.messages()
    assert [c.args for c in tools.of("terraform", "destroy")] == [["terraform", "destroy", "-auto-approve"]]


def test_the_variables_file_and_secrets_are_in_place_before_terraform_destroys(tmp_path: Path, tools):
    root = _tf_two(
        tmp_path, environment_spec="\n  secrets:\n    - key: db_password\n      store: constant\n      value: hunter2"
    )
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True, stages=("apply_infra",))

    assert diagnostics.ok, diagnostics.messages()
    [destroy] = tools.of("terraform", "destroy")
    assert destroy.tfvars_present
    assert destroy.env is not None and "TF_VAR_strata_secrets" in destroy.env
    assert destroy.env["TF_VAR_db_password"] == "hunter2"


def test_destroy_uses_the_destroy_timeout_phase(tmp_path: Path, tools, monkeypatch):
    monkeypatch.setenv("STRATA_TIMEOUT_APPLY_APPS_DESTROY", "7")
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    timeouts = {c.cwd.name: c.timeout for c in tools.of("terraform", "destroy") if c.cwd is not None}
    assert timeouts == {"infra_b": 7, "infra_a": 1800}


def test_a_failing_destroy_stops_the_run_and_keeps_the_recorded_outputs(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}, "apply_apps": {"k": "v"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)
    tools.responders.append(
        lambda call: (
            CommandResult(returncode=1, stdout="", stderr="state is locked")
            if call.args[:2] == ["terraform", "destroy"]
            else None
        )
    )

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert not diagnostics.ok
    assert "destroy failed" in diagnostics.errors[0].message
    assert "state is locked" in diagnostics.errors[0].message
    assert tools.terraform() == [("infra_b", "init"), ("infra_b", "destroy")]  # apply_infra was never reached
    assert set(_record(backend)) == {"apply_infra", "apply_apps"}


# ---------------------------------------------------------------------------
# --dry-run: a preview, never a destroy, never a saved plan
# ---------------------------------------------------------------------------


def test_dry_run_previews_every_step_and_destroys_nothing(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, steps = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert tools.terraform() == [
        ("infra_b", "init"),
        ("infra_b", "plan"),
        ("infra_a", "init"),
        ("infra_a", "plan"),
    ]
    assert [c.args for c in tools.of("terraform", "plan")] == [["terraform", "plan", "-destroy"]] * 2
    assert any(s.startswith("planned destroy of step 'apply_apps'") for s in steps)


def test_neither_a_dry_run_nor_a_destroy_touches_a_saved_apply_plan(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)
    saved_plan = build_path / "infra_a" / "apply_infra.tfplan"
    saved_plan.write_text("a reviewed apply plan", encoding="utf-8")

    _destroy(root, build_path, dry_run=True)
    assert saved_plan.read_text(encoding="utf-8") == "a reviewed apply plan"

    _destroy(root, build_path, force=True)
    assert saved_plan.read_text(encoding="utf-8") == "a reviewed apply plan"
    assert not any("-out" in call.args for call in tools.calls)
    assert not any(call.args[:2] == ["terraform", "apply"] for call in tools.calls)


def test_a_step_with_no_destroy_preview_warns_and_the_run_continues(tmp_path: Path, tools, monkeypatch):
    def _no_preview(self, path, **kwargs):
        raise IntegrationError("no preview here")

    monkeypatch.setattr(TerraformIntegration, "plan_destroy", _no_preview)
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings") == ["destroy_preview_unavailable"] * 2
    assert tools.terraform() == [("infra_b", "init"), ("infra_a", "init")]


def test_a_failing_destroy_preview_is_an_error(tmp_path: Path, tools):
    root = _tf_two(tmp_path)
    build_path = _built(tmp_path, root)
    tools.responders.append(
        lambda call: (
            CommandResult(returncode=1, stdout="", stderr="no state")
            if call.args[:3] == ["terraform", "plan", "-destroy"]
            else None
        )
    )

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert not diagnostics.ok
    assert "destroy plan failed" in diagnostics.errors[0].message


# ---------------------------------------------------------------------------
# Container steps
# ---------------------------------------------------------------------------


def _release(build_path: Path) -> str:
    meta = yaml.safe_load((build_path / "apps" / "auth" / "meta.yaml").read_text(encoding="utf-8"))
    return str(meta.get("releaseName") or "auth")


def test_a_helm_step_is_uninstalled_before_the_infrastructure_it_runs_on(tmp_path: Path, tools):
    root = _helm_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    helm = [c.args for c in tools.of("helm")]
    assert helm == [["helm", "uninstall", "--namespace", "apps", _release(build_path)]]
    order = [c.args[:2] for c in tools.calls if c.args[:2] in (["helm", "uninstall"], ["terraform", "destroy"])]
    assert order == [["helm", "uninstall"], ["terraform", "destroy"]]
    assert tools.terraform() == [("infra_a", "init"), ("infra_a", "destroy")]


def test_a_helm_dry_run_lists_the_resources_and_never_the_secret_data(tmp_path: Path, tools):
    root = _helm_solution(tmp_path)
    build_path = _built(tmp_path, root)
    manifest = (
        "---\napiVersion: v1\nkind: Service\nmetadata:\n  name: demo-svc\n"
        "---\napiVersion: v1\nkind: Secret\nmetadata:\n  name: demo-secret\ndata:\n  token: c3VwZXJzZWNyZXQ=\n"
    )
    tools.responders.append(
        lambda call: (
            CommandResult(returncode=0, stdout=manifest, stderr="")
            if call.args[:3] == ["helm", "get", "manifest"]
            else None
        )
    )

    diagnostics, steps = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert [c.args[1] for c in tools.of("helm")] == ["get"]  # nothing was uninstalled
    shown = "\n".join(steps)
    assert "Service/demo-svc" in shown and "Secret/demo-secret" in shown
    assert "c3VwZXJzZWNyZXQ=" not in shown


def test_a_compose_stack_is_removed_by_name(tmp_path: Path, tools):
    root = _compose_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert [c.args for c in tools.of("docker")] == [["docker", "stack", "rm", "--detach=false", "apps"]]


def test_a_failing_helm_uninstall_stops_the_run(tmp_path: Path, tools):
    root = _helm_solution(tmp_path)
    build_path = _built(tmp_path, root)
    tools.responders.append(
        lambda call: (
            CommandResult(returncode=1, stdout="", stderr="forbidden")
            if call.args[:2] == ["helm", "uninstall"]
            else None
        )
    )

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert not diagnostics.ok
    assert "forbidden" in diagnostics.errors[0].message
    assert tools.terraform() == []  # the infrastructure step after it never ran


# ---------------------------------------------------------------------------
# GitOps
# ---------------------------------------------------------------------------


def _gitops_solution(tmp_path: Path, url: str = "https://example.com/org/gitops-config.git") -> Path:
    return _solution(
        tmp_path,
        manifest=(
            "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec:\n"
            "  remotes:\n    - name: gitops-config\n      type: git\n"
            f"      url: {url}\n      reference: main\n"
        ),
        provisioners=(
            "    - name: forge_sync\n      tool: argocd\n      gitops:\n        remote: gitops-config\n"
            "        output_file: apps/forge/values.yaml\n      output:\n        template: templates/forge.yaml.j2\n"
        ),
        execution="    - name: sync\n      provisioner: forge_sync\n      resources:\n        - r1\n",
        files={"templates/forge.yaml.j2": "replicas: {{ provisioner.name }}\n"},
    )


def test_a_gitops_step_is_destroyed_with_its_resolved_values(tmp_path: Path, tools, monkeypatch):
    captured: dict[str, Any] = {}

    def _destroy_step(self, path, **kwargs):
        captured.update(kwargs)
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ArgoCDIntegration, "destroy", _destroy_step)
    root = _gitops_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert captured["auto_approve"] is True
    assert captured["resolved_values"] is not None
    assert captured["auth"] is None  # the remote names no integration


def test_a_gitops_preview_is_shown_without_follow(tmp_path: Path, tools, monkeypatch):
    def _preview(self, path, **kwargs):
        return CommandResult(returncode=0, stdout="would remove apps/forge/values.yaml from gitops-config", stderr="")

    monkeypatch.setattr(ArgoCDIntegration, "plan_destroy", _preview)
    root = _gitops_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics, steps = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert "would remove apps/forge/values.yaml from gitops-config" in steps


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_gitops_step_goes_from_build_to_deploy_to_destroy_against_a_real_repo(tmp_path: Path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _gitops_solution(tmp_path, url=bare_repo.as_posix())
    build_path = _built(tmp_path, root)

    deployed = deploy_run(_context(root), "app", build_path, force=True)
    assert deployed.ok, deployed.messages()
    verify_dir = tmp_path / "verify"
    subprocess.run(["git", "clone", str(bare_repo), str(verify_dir)], check=True, capture_output=True)
    pushed_file = verify_dir / "apps" / "forge" / "values.yaml"
    assert pushed_file.read_text() == "replicas: forge_sync\n"

    preview, steps = _destroy(root, build_path, dry_run=True)
    assert preview.ok, preview.messages()
    assert "would remove apps/forge/values.yaml from gitops-config" in steps
    subprocess.run(["git", "-C", str(verify_dir), "pull"], check=True, capture_output=True)
    assert pushed_file.exists()  # a preview removes nothing

    destroyed, _ = _destroy(root, build_path, force=True)
    assert destroyed.ok, destroyed.messages()
    subprocess.run(["git", "-C", str(verify_dir), "pull"], check=True, capture_output=True)
    assert not pushed_file.exists()


# ---------------------------------------------------------------------------
# The durable output record
# ---------------------------------------------------------------------------


def test_a_successful_destroy_clears_the_destroyed_steps_recorded_outputs(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}, "apply_apps": {"k": "v"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _record(backend) == {}


def test_a_partial_destroy_clears_only_the_steps_it_destroyed(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}, "apply_apps": {"k": "v"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    diagnostics, _ = _destroy(root, build_path, force=True, stages=("apply_apps",))

    assert diagnostics.ok, diagnostics.messages()
    assert set(_record(backend)) == {"apply_infra"}


def test_a_dry_run_clears_nothing(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}, "apply_apps": {"k": "v"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    assert set(_record(backend)) == {"apply_infra", "apply_apps"}


def test_a_failure_to_clear_the_record_is_a_warning_not_a_failed_destroy(tmp_path: Path, tools, monkeypatch):
    backend = _register_output_store(monkeypatch)
    _seed(backend, {"apply_infra": {"vm_ip": "10.0.0.5"}, "apply_apps": {"k": "v"}})
    root, build_path = _chained(tmp_path, workspace_extra=_STORE)

    def _busy(self, steps):
        raise SystemError("the backend is busy")

    monkeypatch.setattr(deploy_module.DeploymentOutputStore, "clear", _busy)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _codes(diagnostics, "warnings")[-1] == "output_store_clear_failed"
    assert "stale records may remain" in diagnostics.warnings[-1].message.lower()


# ---------------------------------------------------------------------------
# A real terraform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "binary"),
    [
        pytest.param(
            "terraform",
            "terraform",
            marks=pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not on PATH"),
        ),
        # OpenTofu runs the same contract (ADR-0060): the same steps, through `tofu`.
        pytest.param(
            "opentofu", "tofu", marks=pytest.mark.skipif(shutil.which("tofu") is None, reason="tofu not on PATH")
        ),
    ],
)
def test_a_real_terraform_destroy_removes_what_deploy_run_created(tmp_path: Path, tool: str, binary: str):
    root = _solution(
        tmp_path,
        provisioners=f"    - name: tf_main\n      tool: {tool}\n      source:\n        source_path: infra\n",
        execution="    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n",
        files={
            "infra/main.tf": (
                'resource "terraform_data" "a" {\n  input = "one"\n}\n\n'
                'resource "terraform_data" "b" {\n  input = terraform_data.a.id\n}\n'
            )
        },
    )
    build_path = _built(tmp_path, root)
    step_dir = build_path / "infra"

    def _state() -> list[str]:
        result = subprocess.run([binary, "state", "list"], cwd=step_dir, capture_output=True, text=True, check=False)
        return [line for line in result.stdout.splitlines() if line.strip()]

    applied = deploy_run(_context(root), "app", build_path, force=True)
    assert applied.ok, applied.messages()
    assert _state() == ["terraform_data.a", "terraform_data.b"]

    preview = deploy_run(_context(root), "app", build_path, destroy=True, dry_run=True)
    assert preview.ok, preview.messages()
    assert _state() == ["terraform_data.a", "terraform_data.b"]  # a preview changes nothing

    destroyed = deploy_run(_context(root), "app", build_path, destroy=True, force=True)
    assert destroyed.ok, destroyed.messages()
    assert _state() == []


def _pinned_to_a_kube_context(root: Path, tmp_path: Path, monkeypatch) -> None:
    """Name `forge-prd` as the deployment's kube context and give the run a kubeconfig that defines it."""
    deployment = root / "deployment.yaml"
    deployment.write_text(deployment.read_text(encoding="utf-8") + "  kube_context: forge-prd\n", encoding="utf-8")
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("contexts:\n  - name: forge-prd\n  - name: forge-dev\n", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    monkeypatch.delenv("HELM_KUBECONTEXT", raising=False)
    monkeypatch.delenv("HELM_KUBEAPISERVER", raising=False)


def test_a_helm_uninstall_runs_against_the_deployments_kube_context(tmp_path: Path, tools, monkeypatch):
    root = _helm_solution(tmp_path)
    _pinned_to_a_kube_context(root, tmp_path, monkeypatch)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    [uninstall] = tools.of("helm", "uninstall")
    assert uninstall.env is not None and uninstall.env["HELM_KUBECONTEXT"] == "forge-prd"


def test_a_helm_destroy_preview_runs_against_the_deployments_kube_context(tmp_path: Path, tools, monkeypatch):
    root = _helm_solution(tmp_path)
    _pinned_to_a_kube_context(root, tmp_path, monkeypatch)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    [preview] = tools.of("helm", "get")
    assert preview.env is not None and preview.env["HELM_KUBECONTEXT"] == "forge-prd"


def test_a_destroy_stops_before_any_tool_when_the_kube_context_is_missing(tmp_path: Path, tools, monkeypatch):
    root = _helm_solution(tmp_path)
    _pinned_to_a_kube_context(root, tmp_path, monkeypatch)
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert _codes(diagnostics, "errors") == ["helm_kube_context_missing"]
    assert tools.of("helm") == [] and tools.terraform() == []


def _pinned_to_a_docker_context(root: Path, monkeypatch) -> None:
    deployment = root / "deployment.yaml"
    deployment.write_text(deployment.read_text(encoding="utf-8") + "  docker_context: hearth-prd\n", encoding="utf-8")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)


def test_a_stack_removal_runs_against_the_deployments_docker_context(tmp_path: Path, tools, monkeypatch):
    root = _compose_solution(tmp_path)
    _pinned_to_a_docker_context(root, monkeypatch)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, force=True)

    assert diagnostics.ok, diagnostics.messages()
    [removal] = [c for c in tools.of("docker") if c.args[1:3] == ["stack", "rm"]]
    assert removal.env is not None and removal.env["DOCKER_CONTEXT"] == "hearth-prd"


def test_a_stack_removal_preview_runs_against_the_deployments_docker_context(tmp_path: Path, tools, monkeypatch):
    root = _compose_solution(tmp_path)
    _pinned_to_a_docker_context(root, monkeypatch)
    build_path = _built(tmp_path, root)

    diagnostics, _ = _destroy(root, build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    [listing] = [c for c in tools.of("docker") if c.args[1:3] == ["stack", "services"]]
    assert listing.env is not None and listing.env["DOCKER_CONTEXT"] == "hearth-prd"
