"""Unit tests for AuditController — local write operations (Step 4)."""

import json
from pathlib import Path

from strata.controllers.audit_controller import (
    BUILTIN_PATH_DEFINITIONS,
    AuditController,
)
from strata.models.deploy_log_model import (
    DeployLogModel,
    DeployLogStageModel,
    DeployLogStepModel,
)


def _make_payload(**kwargs) -> DeployLogModel:
    defaults = dict(
        execution_id="550e8400-e29b-41d4-a716-446655440000",
        timestamp="2026-06-24T14:32:00Z",
        version="0.13.0",
        deployment="xyz_platform_prd",
        workspace="xyz_platform",
        environment="prd",
        file="deploy/deploy-prd.yaml",
        success=True,
        duration_seconds=164.0,
        stages=[
            DeployLogStageModel(
                name="infrastructure",
                provisioner="terraform",
                success=True,
                started_at="2026-06-24T14:32:00Z",
                completed_at="2026-06-24T14:34:00Z",
                duration_seconds=120.0,
                steps=[
                    DeployLogStepModel(step="setup", success=True, duration_seconds=2.0),
                    DeployLogStepModel(step="plan", success=True, duration_seconds=45.0),
                    DeployLogStepModel(step="apply", success=True, duration_seconds=73.0),
                ],
            ),
            DeployLogStageModel(
                name="platform",
                provisioner="helm",
                success=True,
                started_at="2026-06-24T14:34:00Z",
                completed_at="2026-06-24T14:35:00Z",
                duration_seconds=60.0,
            ),
        ],
    )
    defaults.update(kwargs)
    return DeployLogModel(**defaults)


class TestGenerateExecutionId:
    def test_returns_uuid_string(self):
        eid = AuditController.generate_execution_id()
        assert len(eid) == 36
        assert eid.count("-") == 4

    def test_unique(self):
        ids = {AuditController.generate_execution_id() for _ in range(100)}
        assert len(ids) == 100


class TestResolveOutputDir:
    def test_by_execution(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        result = controller._resolve_output_dir("by-execution", {}, tmp_path, payload)
        # Colons in timestamp are replaced with - for filesystem safety
        assert result == tmp_path / "xyz_platform_prd" / "2026-06-24T14-32-00Z"

    def test_by_stage_without_stage(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        # "by-stage" template has {{ stage }} which is empty → segment stripped
        result = controller._resolve_output_dir("by-stage", {}, tmp_path, payload)
        assert result == tmp_path / "xyz_platform_prd"

    def test_flat(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        result = controller._resolve_output_dir("flat", {}, tmp_path, payload)
        assert result == tmp_path / "xyz_platform_prd"

    def test_custom_named_path(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        custom_defs = {"custom": "{{ environment }}/{{ deployment }}"}
        result = controller._resolve_output_dir("custom", custom_defs, tmp_path, payload)
        assert result == tmp_path / "prd" / "xyz_platform_prd"

    def test_inline_jinja2_template(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        result = controller._resolve_output_dir("{{ deployment }}/{{ date }}", {}, tmp_path, payload)
        assert result == tmp_path / "xyz_platform_prd" / "2026-06-24"

    def test_missing_optional_tokens_stripped(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        # "full" has {{ tenant }} which is empty → segment stripped
        result = controller._resolve_output_dir("full", {}, tmp_path, payload)
        # tenant empty → stripped, workspace + deployment + timestamp remain
        assert "xyz_platform" in str(result)
        assert "xyz_platform_prd" in str(result)


class TestWriteDeployLog:
    def test_writes_execution_json(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        base_path = tmp_path / "deploy-log"

        success, exec_path = controller.write_deploy_log(
            payload=payload,
            base_path=base_path,
            structure="flat",
            file_per_stage=False,
        )

        assert success is True
        assert exec_path is not None
        assert exec_path.exists()
        assert exec_path.name == "_execution.json"

        data = json.loads(exec_path.read_text())
        assert data["execution_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert data["deployment"] == "xyz_platform_prd"
        assert data["success"] is True

    def test_writes_stage_files(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        base_path = tmp_path / "deploy-log"

        success, exec_path = controller.write_deploy_log(
            payload=payload,
            base_path=base_path,
            structure="flat",
            file_per_stage=True,
        )

        assert success is True
        output_dir = exec_path.parent
        assert (output_dir / "infrastructure.json").exists()
        assert (output_dir / "platform.json").exists()

        # Verify stage file content
        infra_data = json.loads((output_dir / "infrastructure.json").read_text())
        assert infra_data["execution_id"] == payload.execution_id
        assert infra_data["stage"]["name"] == "infrastructure"
        assert infra_data["stage"]["provisioner"] == "terraform"

    def test_no_stage_files_when_disabled(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        base_path = tmp_path / "deploy-log"

        success, exec_path = controller.write_deploy_log(
            payload=payload,
            base_path=base_path,
            structure="flat",
            file_per_stage=False,
        )

        assert success is True
        output_dir = exec_path.parent
        assert not (output_dir / "infrastructure.json").exists()
        assert not (output_dir / "platform.json").exists()

    def test_by_execution_creates_timestamped_dir(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        base_path = tmp_path / "deploy-log"

        success, exec_path = controller.write_deploy_log(
            payload=payload,
            base_path=base_path,
            structure="by-execution",
        )

        assert success is True
        # Path should be: base_path / deployment / timestamp (sanitized) / _execution.json
        assert "xyz_platform_prd" in str(exec_path)
        assert "2026-06-24T14-32-00Z" in str(exec_path)

    def test_custom_path_definitions(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        base_path = tmp_path / "deploy-log"
        custom_defs = {"env-deploy": "{{ environment }}/{{ deployment }}"}

        success, exec_path = controller.write_deploy_log(
            payload=payload,
            base_path=base_path,
            structure="env-deploy",
            path_definitions=custom_defs,
        )

        assert success is True
        assert "prd" in str(exec_path)
        assert "xyz_platform_prd" in str(exec_path)

    def test_creates_directories(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()
        base_path = tmp_path / "deep" / "nested" / "deploy-log"

        success, exec_path = controller.write_deploy_log(
            payload=payload,
            base_path=base_path,
            structure="flat",
        )

        assert success is True
        assert exec_path.exists()

    def test_no_errors_on_success(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        payload = _make_payload()

        controller.write_deploy_log(
            payload=payload,
            base_path=tmp_path / "deploy-log",
            structure="flat",
        )

        assert not controller.has_errors()


class TestQueryDeployLogs:
    def _write_entries(self, base_path: Path, entries: list[dict]) -> None:
        for i, entry in enumerate(entries):
            payload = DeployLogModel(**entry)
            # Use execution_id as dir name to avoid filesystem-unsafe chars
            output_dir = base_path / payload.deployment / f"entry-{i}"
            output_dir.mkdir(parents=True, exist_ok=True)
            exec_path = output_dir / "_execution.json"
            exec_path.write_text(
                json.dumps(payload.model_dump(exclude_none=True), indent=2),
                encoding="utf-8",
            )

    def test_empty_directory(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        results = controller.query_deploy_logs(tmp_path / "nonexistent")
        assert results == []

    def test_finds_entries(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        base = tmp_path / "deploy-log"

        self._write_entries(
            base,
            [
                dict(
                    execution_id="id-1",
                    timestamp="2026-06-24T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=True,
                    duration_seconds=100.0,
                ),
                dict(
                    execution_id="id-2",
                    timestamp="2026-06-25T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=False,
                    duration_seconds=50.0,
                ),
            ],
        )

        results = controller.query_deploy_logs(base)
        assert len(results) == 2
        # Sorted descending by timestamp
        assert results[0].execution_id == "id-2"
        assert results[1].execution_id == "id-1"

    def test_filter_since(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        base = tmp_path / "deploy-log"

        self._write_entries(
            base,
            [
                dict(
                    execution_id="old",
                    timestamp="2026-06-20T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=True,
                    duration_seconds=100.0,
                ),
                dict(
                    execution_id="new",
                    timestamp="2026-06-25T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=True,
                    duration_seconds=100.0,
                ),
            ],
        )

        results = controller.query_deploy_logs(base, since="2026-06-24")
        assert len(results) == 1
        assert results[0].execution_id == "new"

    def test_filter_stage(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        base = tmp_path / "deploy-log"

        self._write_entries(
            base,
            [
                dict(
                    execution_id="has-infra",
                    timestamp="2026-06-24T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=True,
                    duration_seconds=100.0,
                    stages=[
                        dict(
                            name="infrastructure",
                            success=True,
                            started_at="2026-06-24T10:00:00Z",
                            completed_at="2026-06-24T10:01:00Z",
                            duration_seconds=60.0,
                        )
                    ],
                ),
                dict(
                    execution_id="no-infra",
                    timestamp="2026-06-25T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=True,
                    duration_seconds=100.0,
                    stages=[
                        dict(
                            name="platform",
                            success=True,
                            started_at="2026-06-25T10:00:00Z",
                            completed_at="2026-06-25T10:01:00Z",
                            duration_seconds=60.0,
                        )
                    ],
                ),
            ],
        )

        results = controller.query_deploy_logs(base, stage="infrastructure")
        assert len(results) == 1
        assert results[0].execution_id == "has-infra"

    def test_filter_last(self, tmp_path):
        controller = AuditController(work_path=tmp_path)
        base = tmp_path / "deploy-log"

        self._write_entries(
            base,
            [
                dict(
                    execution_id=f"id-{i}",
                    timestamp=f"2026-06-{20 + i:02d}T10:00:00Z",
                    version="0.13.0",
                    deployment="xyz_platform_prd",
                    file="deploy.yaml",
                    success=True,
                    duration_seconds=100.0,
                )
                for i in range(5)
            ],
        )

        results = controller.query_deploy_logs(base, last=2)
        assert len(results) == 2
        # Most recent first
        assert results[0].execution_id == "id-4"
        assert results[1].execution_id == "id-3"


class TestBuiltinPathDefinitions:
    def test_all_definitions_are_valid_templates(self):
        for name, template in BUILTIN_PATH_DEFINITIONS.items():
            assert "{{" in template, f"{name} should be a Jinja2 template"

    def test_expected_keys(self):
        assert "flat" in BUILTIN_PATH_DEFINITIONS
        assert "by-stage" in BUILTIN_PATH_DEFINITIONS
        assert "by-execution" in BUILTIN_PATH_DEFINITIONS
        assert "by-tenant" in BUILTIN_PATH_DEFINITIONS
        assert "full" in BUILTIN_PATH_DEFINITIONS
