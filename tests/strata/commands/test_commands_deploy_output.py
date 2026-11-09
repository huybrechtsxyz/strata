#!/usr/bin/env python3
"""Tests for `strata deploy output` (docs/work/deployment-documentation.md, section 1)."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.controllers.deployment_output_store import DeploymentOutputStore
from strata.controllers.deployment_report_store import DeploymentReportStore
from strata.integrations import registry as registry_module
from strata.integrations.capabilities import KeyValueStoreIntegration
from strata.integrations.resolved_context import KeyValueEntry

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"
_SECRET = "s3cr3t-Pa55word"


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


@pytest.fixture(autouse=True)
def _backend(monkeypatch):
    _Backend.data = {}
    monkeypatch.setattr(
        registry_module, "entry_points", lambda group: [_EntryPoint(name="fake-output-store", value="x:Y")]
    )


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    (root / relative).write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, output_store: bool = True, max_age: int | None = None) -> Path:
    """Deployment `app`, workspace with steps `infra` and `apps`."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    a: b\n",
    )
    store = "  output_store: fake-output-store\n" if output_store else ""
    if max_age is not None:
        store += f"  output_store_max_age: {max_age}\n"
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        f"{store}  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n"
        "    - name: infra\n      provisioner: tf\n      resources:\n        - r1\n"
        "    - name: apps\n      provisioner: tf\n      resources:\n        - r1\n      depends_on:\n        - infra\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(root, "app.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    return root


def _deploy_infra(*, collected_at: datetime | None = None, report: bool = True) -> None:
    """What `deploy run` records for step `infra`: one plain and one sensitive output."""
    at = collected_at or datetime.now(timezone.utc)
    backend = _Backend()
    DeploymentOutputStore(backend, "app").record(
        "infra",
        {"fqdn": "sql.example.net", "conn": _SECRET},
        {"conn"},
        collected_at=at,
    )
    if report:
        DeploymentReportStore(backend, "app").record(
            "infra",
            sensitive_keys={"conn"},
            descriptions={"conn": "Also in kv-web as sql-conn", "fqdn": "SQL server FQDN"},
            collected_at=at,
        )


def _invoke(root: Path, *args: str):
    return CliRunner().invoke(cli, ["deploy", "output", "app", *args, "--path", str(root)])


def _data(result) -> dict:
    assert result.exit_code == EXIT_SUCCESS, result.output
    return json.loads(result.output)["data"]


def test_table_lists_every_step_with_sensitive_values_hidden(tmp_path):
    root = _solution(tmp_path)
    _deploy_infra()

    result = _invoke(root)

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "sql.example.net" in result.output
    assert "conn  <sensitive>" in result.output
    assert "apps  no outputs recorded" in result.output
    assert _SECRET not in result.output
    assert "SQL server FQDN" not in result.output  # descriptions only in the JSON data


def test_json_carries_descriptions_and_never_a_sensitive_value(tmp_path):
    root = _solution(tmp_path)
    _deploy_infra()

    data = _data(_invoke(root, "-o", "json"))

    steps = {step["step"]: step for step in data["steps"]}
    assert steps["apps"]["status"] == "not_recorded"
    infra = steps["infra"]
    assert (infra["status"], infra["report"]) == ("recorded", "recorded")
    assert infra["outputs"] == [
        {"name": "conn", "value": None, "sensitive": True, "description": "Also in kv-web as sql-conn"},
        {"name": "fqdn", "value": "sql.example.net", "sensitive": False, "description": "SQL server FQDN"},
    ]
    assert _SECRET not in json.dumps(data)


def test_one_step_and_one_key(tmp_path):
    root = _solution(tmp_path)
    _deploy_infra()

    assert [step["step"] for step in _data(_invoke(root, "infra", "-o", "json"))["steps"]] == ["infra"]
    one = _data(_invoke(root, "infra.fqdn", "-o", "json"))["steps"][0]["outputs"]
    assert [row["name"] for row in one] == ["fqdn"]


def test_format_raw_prints_only_the_bare_value(tmp_path):
    root = _solution(tmp_path)
    _deploy_infra()

    result = _invoke(root, "infra.fqdn", "--format", "raw")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert result.stdout == "sql.example.net\n"


def test_format_raw_on_a_sensitive_output_fails_and_shows_its_description(tmp_path):
    root = _solution(tmp_path)
    _deploy_infra()

    result = _invoke(root, "infra.conn", "--format", "raw")

    assert result.exit_code == EXIT_USAGE
    assert "is sensitive" in result.output and "Also in kv-web as sql-conn" in result.output
    assert _SECRET not in result.output


@pytest.mark.parametrize("args", [(), ("infra",), ("--format", "raw")])
def test_format_raw_needs_one_step_key(tmp_path, args):
    root = _solution(tmp_path)
    _deploy_infra()
    result = _invoke(root, *args, "--format", "raw") if "--format" not in args else _invoke(root, *args)
    assert result.exit_code == EXIT_USAGE
    assert "needs one STEP.KEY" in result.output


@pytest.mark.parametrize(
    ("address", "message"),
    [
        ("nope", "No execution step 'nope'"),
        ("nope.key", "No execution step 'nope'"),
        ("infra.missing", "has no output 'missing'"),
        ("apps.fqdn", "has no outputs recorded yet"),
    ],
)
def test_unknown_addresses_are_usage_errors(tmp_path, address, message):
    root = _solution(tmp_path)
    _deploy_infra()
    result = _invoke(root, address)
    assert result.exit_code == EXIT_USAGE
    assert message in result.output


def test_without_an_output_store_the_command_fails_validation(tmp_path):
    result = _invoke(_solution(tmp_path, output_store=False))
    assert result.exit_code == EXIT_VALIDATION
    assert "sets no output_store" in result.output


def test_a_destroyed_step_is_shown_as_destroyed(tmp_path):
    root = _solution(tmp_path)
    _deploy_infra()
    backend = _Backend()
    DeploymentOutputStore(backend, "app").clear(["infra"])
    DeploymentReportStore(backend, "app").clear(["infra"])

    steps = {step["step"]: step for step in _data(_invoke(root, "-o", "json"))["steps"]}
    assert (steps["infra"]["status"], steps["infra"]["outputs"]) == ("destroyed", [])
    assert _invoke(root, "infra.fqdn").exit_code == EXIT_USAGE


def test_an_old_record_is_shown_with_a_warning(tmp_path):
    root = _solution(tmp_path, max_age=60)
    _deploy_infra(collected_at=datetime.now(timezone.utc) - timedelta(hours=1))

    result = _invoke(root, "infra.fqdn", "--format", "raw")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert result.stdout.startswith("sql.example.net\n")
    assert "output_store_max_age" in result.output


def test_a_step_without_report_data_hides_nothing_it_does_not_know_and_warns(tmp_path):
    """An older strata wrote only the output record: the sensitive name is unknown, so not listed."""
    root = _solution(tmp_path)
    _deploy_infra(report=False)

    payload = json.loads(_invoke(root, "infra", "-o", "json").output)

    infra = payload["data"]["steps"][0]
    assert infra["report"] == "not_recorded"
    assert [row["name"] for row in infra["outputs"]] == ["fqdn"]
    assert any("report data" in json.dumps(item) for item in payload.get("diagnostics", payload.get("warnings", [])))


def test_a_corrupt_record_is_a_system_failure(tmp_path):
    root = _solution(tmp_path)
    _Backend.data["strata/step-outputs/app/infra"] = b"not json"
    assert _invoke(root).exit_code == EXIT_FAILURE
