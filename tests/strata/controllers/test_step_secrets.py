#!/usr/bin/env python3
"""Tests for `step_secret_keys()` (docs/work/deployment-documentation.md, section 1): which declared secrets a
non-container step used, per delivery path."""

from pathlib import Path

from strata.controllers.step_secrets import step_secret_keys
from strata.integrations.script import ScriptIntegration
from strata.integrations.terraform import TerraformIntegration
from strata.models.provisioning_model import ProvisionerModel

_DECLARED = {"A", "B", "C", "D"}


def _provisioner(**spec) -> ProvisionerModel:
    return ProvisionerModel.model_validate({"name": "p", "source": {"source_path": "src"}, **spec})


def test_terraform_counts_payload_and_backend_tokens_and_declared_variables(tmp_path: Path):
    (tmp_path / "variables.tf").write_text('variable "C" {}\nvariable "not_a_secret" {}\n', encoding="utf-8")
    provisioner = _provisioner(tool="terraform", backend={"type": "local", "configuration": {"key": "${secret:B}"}})

    keys, every = step_secret_keys(
        provisioner=provisioner,
        integration=TerraformIntegration(),
        path=tmp_path,
        declared=_DECLARED,
        root=tmp_path,
        payload={"resources": {"r1": {"tags": {"x": "${secret:A}"}}}},
    )

    assert (keys, every) == ({"A", "B", "C"}, False)


def test_a_script_without_values_receives_every_secret(tmp_path: Path):
    provisioner = _provisioner(tool="script", script={"file": "run.py"})
    keys, every = step_secret_keys(
        provisioner=provisioner, integration=ScriptIntegration(), path=tmp_path, declared=_DECLARED, root=tmp_path
    )
    assert (keys, every) == (_DECLARED, True)


def test_a_script_with_values_uses_the_listed_keys_and_its_args(tmp_path: Path):
    provisioner = _provisioner(
        tool="script", script={"file": "run.py", "args": ["--pwd", "${secret:D}"], "values": ["A", "NOT_SECRET"]}
    )
    keys, every = step_secret_keys(
        provisioner=provisioner, integration=ScriptIntegration(), path=tmp_path, declared=_DECLARED, root=tmp_path
    )
    assert (keys, every) == ({"A", "D"}, False)


def test_an_output_template_adds_the_secrets_it_reads_or_all_when_dynamic(tmp_path: Path):
    (tmp_path / "static.j2").write_text("{{ secrets.B }}", encoding="utf-8")
    (tmp_path / "dynamic.j2").write_text("{% for k, v in secrets.items() %}{{ k }}{% endfor %}", encoding="utf-8")

    def _run(template: str):
        return step_secret_keys(
            provisioner=_provisioner(tool="terraform", output={"template": template}),
            integration=TerraformIntegration(),
            path=tmp_path,
            declared=_DECLARED,
            root=tmp_path,
            payload={},
        )

    assert _run("static.j2") == ({"B"}, False)
    assert _run("dynamic.j2") == (_DECLARED, True)
