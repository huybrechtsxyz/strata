#!/usr/bin/env python3
"""Tests for `read_declared_outputs()` (docs/design/deployment-documentation.md, section 3)."""

from pathlib import Path

from strata.utils.terraform_outputs import DeclaredOutput, read_declared_outputs


def test_reads_description_and_sensitive_flag_of_each_root_module_output(tmp_path: Path):
    (tmp_path / "outputs.tf").write_text(
        'output "conn" {\n  value = "x"\n  description = "Also in \\"kv-web\\" as sql-conn"\n  sensitive = true\n}\n'
        'output "fqdn" {\n  value = "y"\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "main.tf").write_text(
        'output "ip" {\n  value = "z"\n  description = "Public IP"\n}\n', encoding="utf-8"
    )

    assert read_declared_outputs(tmp_path) == {
        "conn": DeclaredOutput(name="conn", description='Also in "kv-web" as sql-conn', sensitive=True),
        "fqdn": DeclaredOutput(name="fqdn", description=None, sensitive=False),
        "ip": DeclaredOutput(name="ip", description="Public IP", sensitive=False),
    }


def test_ignores_child_modules_and_unparsable_files(tmp_path: Path):
    (tmp_path / "modules" / "child").mkdir(parents=True)
    (tmp_path / "modules" / "child" / "outputs.tf").write_text('output "inner" {\n  value = 1\n}\n', encoding="utf-8")
    (tmp_path / "broken.tf").write_text("output {{{ not hcl", encoding="utf-8")
    (tmp_path / "ok.tf").write_text('output "outer" {\n  value = 1\n}\n', encoding="utf-8")

    assert list(read_declared_outputs(tmp_path)) == ["outer"]


def test_a_missing_directory_declares_nothing(tmp_path: Path):
    assert read_declared_outputs(tmp_path / "absent") == {}


def test_reads_outputs_and_variables_from_tf_json_files(tmp_path: Path):
    from strata.utils.terraform_outputs import read_declared_variables

    (tmp_path / "outputs.tf.json").write_text(
        '{"output": {"conn": {"value": "x", "sensitive": true, "description": "In kv-web"}},'
        ' "variable": [{"SQL_PWD": {}}]}',
        encoding="utf-8",
    )
    (tmp_path / "bad.tf.json").write_text("{not json", encoding="utf-8")

    assert read_declared_outputs(tmp_path) == {
        "conn": DeclaredOutput(name="conn", description="In kv-web", sensitive=True)
    }
    assert read_declared_variables(tmp_path) == {"SQL_PWD"}
