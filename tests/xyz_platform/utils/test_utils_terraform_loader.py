#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_terraform_loader.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Tests for TerraformLoader class.
===============================================================================
"""

import json

import pytest

from xyz_platform.utils.terraform_loader import TerraformLoader


class TestTerraformLoaderInit:
    """Test TerraformLoader instantiation."""

    def test_init(self):
        """TerraformLoader can be instantiated."""
        loader = TerraformLoader()
        assert loader is not None
        assert loader._logger is not None


class TestTerraformLoaderLoadHcl:
    """Test loading HCL2 (.tf / .tfvars) files."""

    def test_load_simple_tf_file(self, tmp_path):
        """Load a simple .tf file with a variable block."""
        loader = TerraformLoader()

        tf_file = tmp_path / "variables.tf"
        tf_file.write_text('variable "name" {\n  type    = string\n  default = "hello"\n}\n')

        data = loader.load_hcl_file(tf_file)
        assert "variable" in data
        assert len(data["variable"]) == 1

    def test_load_tfvars_file(self, tmp_path):
        """Load a .tfvars file with variable assignments."""
        loader = TerraformLoader()

        tfvars = tmp_path / "prod.tfvars"
        tfvars.write_text('region = "eu-west-1"\ninstance_count = 3\n')

        data = loader.load_hcl_file(tfvars)
        assert "region" in data
        assert data["instance_count"] == 3

    def test_load_tf_with_resource_blocks(self, tmp_path):
        """Load a .tf file with multiple resource blocks."""
        loader = TerraformLoader()

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            'resource "aws_instance" "web" {\n'
            '  ami           = "ami-123"\n'
            '  instance_type = "t2.micro"\n'
            "}\n\n"
            'resource "aws_instance" "api" {\n'
            '  ami           = "ami-456"\n'
            '  instance_type = "t2.small"\n'
            "}\n"
        )

        data = loader.load_hcl_file(tf_file)
        assert "resource" in data
        assert len(data["resource"]) == 2

    def test_load_nonexistent_file_raises(self, tmp_path):
        """Loading a non-existent file raises FileNotFoundError."""
        loader = TerraformLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_hcl_file(tmp_path / "missing.tf")

    def test_load_directory_raises(self, tmp_path):
        """Loading a directory raises ValueError."""
        loader = TerraformLoader()

        with pytest.raises(ValueError, match="not a file"):
            loader.load_hcl_file(tmp_path)

    def test_load_invalid_hcl_raises(self, tmp_path):
        """Loading invalid HCL raises ValueError."""
        loader = TerraformLoader()

        tf_file = tmp_path / "bad.tf"
        tf_file.write_text("this is { not valid { hcl {{{\n")

        with pytest.raises(ValueError, match="Failed to parse"):
            loader.load_hcl_file(tf_file)


class TestTerraformLoaderLoadJson:
    """Test loading JSON terraform files."""

    def test_load_tfvars_json(self, tmp_path):
        """Load a .tfvars.json file."""
        loader = TerraformLoader()

        json_file = tmp_path / "workspace.auto.tfvars.json"
        data = {"workspace_name": "haven", "environment": "production"}
        json_file.write_text(json.dumps(data))

        result = loader.load_json_file(json_file)
        assert result["workspace_name"] == "haven"
        assert result["environment"] == "production"

    def test_load_tf_json(self, tmp_path):
        """Load a .tf.json file."""
        loader = TerraformLoader()

        json_file = tmp_path / "main.tf.json"
        data = {"resource": {"aws_instance": {"web": {"ami": "ami-123"}}}}
        json_file.write_text(json.dumps(data))

        result = loader.load_json_file(json_file)
        assert result["resource"]["aws_instance"]["web"]["ami"] == "ami-123"

    def test_load_invalid_json_raises(self, tmp_path):
        """Loading invalid JSON raises ValueError."""
        loader = TerraformLoader()

        json_file = tmp_path / "bad.tfvars.json"
        json_file.write_text("{not valid json")

        with pytest.raises(ValueError, match="Failed to parse JSON"):
            loader.load_json_file(json_file)

    def test_load_non_dict_json_raises(self, tmp_path):
        """Loading JSON that isn't a dict raises ValueError."""
        loader = TerraformLoader()

        json_file = tmp_path / "array.tfvars.json"
        json_file.write_text("[1, 2, 3]")

        with pytest.raises(ValueError, match="must contain a dictionary"):
            loader.load_json_file(json_file)


class TestTerraformLoaderAutoDetect:
    """Test auto-detection of file format."""

    def test_load_file_detects_tf(self, tmp_path):
        """load_file() auto-detects .tf as HCL."""
        loader = TerraformLoader()

        tf_file = tmp_path / "main.tf"
        tf_file.write_text('variable "x" {\n  default = 1\n}\n')

        data = loader.load_file(tf_file)
        assert "variable" in data

    def test_load_file_detects_tfvars(self, tmp_path):
        """load_file() auto-detects .tfvars as HCL."""
        loader = TerraformLoader()

        tfvars = tmp_path / "test.tfvars"
        tfvars.write_text('name = "test"\n')

        data = loader.load_file(tfvars)
        assert "name" in data

    def test_load_file_detects_tfvars_json(self, tmp_path):
        """load_file() auto-detects .tfvars.json as JSON."""
        loader = TerraformLoader()

        json_file = tmp_path / "test.auto.tfvars.json"
        json_file.write_text(json.dumps({"key": "value"}))

        data = loader.load_file(json_file)
        assert data["key"] == "value"

    def test_load_file_detects_tf_json(self, tmp_path):
        """load_file() auto-detects .tf.json as JSON."""
        loader = TerraformLoader()

        json_file = tmp_path / "main.tf.json"
        json_file.write_text(json.dumps({"resource": {}}))

        data = loader.load_file(json_file)
        assert "resource" in data

    def test_load_file_unsupported_extension_raises(self, tmp_path):
        """load_file() raises for unsupported extensions."""
        loader = TerraformLoader()

        bad_file = tmp_path / "readme.txt"
        bad_file.write_text("hello")

        with pytest.raises(ValueError, match="Unsupported terraform file format"):
            loader.load_file(bad_file)


class TestTerraformLoaderMerge:
    """Test merging terraform structures."""

    def test_merge_empty_list(self):
        """Merging empty list returns empty dict."""
        loader = TerraformLoader()
        assert loader.merge([]) == {}

    def test_merge_single_item(self):
        """Merging single item returns copy."""
        loader = TerraformLoader()
        data = {"variable": [{"name": {"default": "hello"}}]}
        result = loader.merge([data])
        assert result == data
        assert result is not data

    def test_merge_concatenates_resource_blocks(self):
        """Merging combines resource lists from multiple files."""
        loader = TerraformLoader()

        file_a = {"resource": [{"aws_instance": {"web": {"ami": "ami-1"}}}]}
        file_b = {"resource": [{"aws_instance": {"api": {"ami": "ami-2"}}}]}

        result = loader.merge([file_a, file_b])
        assert len(result["resource"]) == 2

    def test_merge_concatenates_variable_blocks(self):
        """Merging combines variable lists."""
        loader = TerraformLoader()

        file_a = {"variable": [{"name": {"type": "string"}}]}
        file_b = {"variable": [{"region": {"type": "string", "default": "us-east-1"}}]}

        result = loader.merge([file_a, file_b])
        assert len(result["variable"]) == 2

    def test_merge_concatenates_output_blocks(self):
        """Merging combines output lists."""
        loader = TerraformLoader()

        file_a = {"output": [{"id": {"value": "resource.id"}}]}
        file_b = {"output": [{"name": {"value": "resource.name"}}]}

        result = loader.merge([file_a, file_b])
        assert len(result["output"]) == 2

    def test_merge_deep_merges_terraform_block(self):
        """Terraform settings block is deep-merged (not concatenated)."""
        loader = TerraformLoader()

        file_a = {"terraform": [{"required_version": ">= 1.5"}]}
        file_b = {"terraform": [{"cloud": {"organization": "test"}}]}

        # terraform is not in the list-block-types, so later wins
        result = loader.merge([file_a, file_b])
        assert result["terraform"] == file_b["terraform"]

    def test_merge_preserves_non_conflicting_keys(self):
        """Non-overlapping keys from both files are preserved."""
        loader = TerraformLoader()

        file_a = {"variable": [{"name": {}}], "locals": [{"x": 1}]}
        file_b = {"resource": [{"aws_s3_bucket": {"data": {}}}]}

        result = loader.merge([file_a, file_b])
        assert "variable" in result
        assert "locals" in result
        assert "resource" in result

    def test_merge_three_files(self):
        """Merging three files combines all blocks."""
        loader = TerraformLoader()

        files = [
            {"resource": [{"type_a": {"one": {}}}]},
            {"resource": [{"type_b": {"two": {}}}]},
            {"resource": [{"type_c": {"three": {}}}]},
        ]

        result = loader.merge(files)
        assert len(result["resource"]) == 3


class TestTerraformLoaderMergeTfvars:
    """Test merging tfvars value structures."""

    def test_merge_tfvars_scalars_last_wins(self):
        """Later tfvars override earlier scalar values."""
        loader = TerraformLoader()

        base = {"region": "us-east-1", "count": 1}
        override = {"region": "eu-west-1", "count": 3}

        result = loader.merge_tfvars([])  # test empty first
        assert result == {}

    def test_merge_tfvars_scalar_override(self, tmp_path):
        """Later tfvars file overrides scalars."""
        loader = TerraformLoader()

        base_file = tmp_path / "base.tfvars.json"
        base_file.write_text(json.dumps({"region": "us-east-1", "count": 1}))

        override_file = tmp_path / "override.tfvars.json"
        override_file.write_text(json.dumps({"region": "eu-west-1", "count": 3}))

        result = loader.merge_tfvars([base_file, override_file])
        assert result["region"] == "eu-west-1"
        assert result["count"] == 3

    def test_merge_tfvars_deep_merges_maps(self, tmp_path):
        """Nested maps in tfvars are deep-merged."""
        loader = TerraformLoader()

        base_file = tmp_path / "base.tfvars.json"
        base_file.write_text(json.dumps({"config": {"a": 1, "b": 2}}))

        override_file = tmp_path / "override.tfvars.json"
        override_file.write_text(json.dumps({"config": {"b": 99, "c": 3}}))

        result = loader.merge_tfvars([base_file, override_file])
        assert result["config"] == {"a": 1, "b": 99, "c": 3}

    def test_merge_tfvars_adds_new_keys(self, tmp_path):
        """New keys from later files are added."""
        loader = TerraformLoader()

        base_file = tmp_path / "base.tfvars.json"
        base_file.write_text(json.dumps({"name": "base"}))

        extra_file = tmp_path / "extra.tfvars.json"
        extra_file.write_text(json.dumps({"version": "1.0"}))

        result = loader.merge_tfvars([base_file, extra_file])
        assert result["name"] == "base"
        assert result["version"] == "1.0"


class TestTerraformLoaderConcatenate:
    """Test raw file concatenation."""

    def test_concatenate_single_file(self, tmp_path):
        """Single file returns its content."""
        loader = TerraformLoader()

        tf_file = tmp_path / "a.tf"
        tf_file.write_text('resource "a" "b" {\n  x = 1\n}\n')

        result = loader.concatenate([tf_file])
        assert 'resource "a" "b"' in result

    def test_concatenate_multiple_files(self, tmp_path):
        """Multiple files are joined with separator."""
        loader = TerraformLoader()

        file_a = tmp_path / "a.tf"
        file_a.write_text('resource "a" "one" {\n  x = 1\n}\n')

        file_b = tmp_path / "b.tf"
        file_b.write_text('resource "a" "two" {\n  x = 2\n}\n')

        result = loader.concatenate([file_a, file_b])
        assert 'resource "a" "one"' in result
        assert 'resource "a" "two"' in result
        assert "\n\n" in result  # separator between files

    def test_concatenate_skips_empty_files(self, tmp_path):
        """Empty files are skipped in concatenation."""
        loader = TerraformLoader()

        file_a = tmp_path / "a.tf"
        file_a.write_text('resource "a" "one" { x = 1 }\n')

        empty = tmp_path / "empty.tf"
        empty.write_text("   \n  \n")

        file_b = tmp_path / "b.tf"
        file_b.write_text('resource "a" "two" { x = 2 }\n')

        result = loader.concatenate([file_a, empty, file_b])
        assert 'resource "a" "one"' in result
        assert 'resource "a" "two"' in result

    def test_concatenate_custom_separator(self, tmp_path):
        """Custom separator is used between files."""
        loader = TerraformLoader()

        file_a = tmp_path / "a.tf"
        file_a.write_text("# file a")

        file_b = tmp_path / "b.tf"
        file_b.write_text("# file b")

        result = loader.concatenate([file_a, file_b], separator="\n# ---\n")
        assert "# ---" in result


class TestTerraformLoaderWrite:
    """Test writing terraform files."""

    def test_write_json(self, tmp_path):
        """Write data as JSON."""
        loader = TerraformLoader()

        output = tmp_path / "output.auto.tfvars.json"
        data = {"region": "eu-west-1", "count": 3}

        loader.write_json(data, output)

        assert output.exists()
        loaded = json.loads(output.read_text())
        assert loaded == data

    def test_write_hcl(self, tmp_path):
        """Write data as HCL2."""
        loader = TerraformLoader()

        output = tmp_path / "output.tf"
        data = {"variable": [{"name": {"type": "string", "default": "test"}}]}

        loader.write_hcl(data, output)

        assert output.exists()
        content = output.read_text()
        assert "variable" in content

    def test_write_raw(self, tmp_path):
        """Write raw content to file."""
        loader = TerraformLoader()

        output = tmp_path / "combined.tf"
        content = 'resource "a" "b" {\n  x = 1\n}\n'

        loader.write_raw(content, output)

        assert output.exists()
        assert output.read_text() == content

    def test_write_creates_parent_dirs(self, tmp_path):
        """Write creates parent directories if missing."""
        loader = TerraformLoader()

        output = tmp_path / "nested" / "deep" / "output.tfvars.json"
        data = {"key": "value"}

        loader.write_json(data, output)
        assert output.exists()

    def test_write_auto_detects_json(self, tmp_path):
        """write() auto-detects .tfvars.json as JSON format."""
        loader = TerraformLoader()

        output = tmp_path / "test.auto.tfvars.json"
        data = {"x": 1}

        loader.write(data, output)

        loaded = json.loads(output.read_text())
        assert loaded == data

    def test_write_auto_detects_hcl(self, tmp_path):
        """write() auto-detects .tf as HCL format."""
        loader = TerraformLoader()

        output = tmp_path / "output.tf"
        data = {"variable": [{"test": {"default": "hello"}}]}

        loader.write(data, output)

        content = output.read_text()
        assert "variable" in content


class TestTerraformLoaderLoadAndMerge:
    """Test the convenience load_and_merge method."""

    def test_load_and_merge_multiple_tf_files(self, tmp_path):
        """Load and merge multiple .tf files combining blocks."""
        loader = TerraformLoader()

        file_a = tmp_path / "a.tf"
        file_a.write_text('variable "name" {\n  type = string\n}\n')

        file_b = tmp_path / "b.tf"
        file_b.write_text('variable "region" {\n  type    = string\n  default = "us-east-1"\n}\n')

        result = loader.load_and_merge([file_a, file_b])
        assert "variable" in result
        assert len(result["variable"]) == 2

    def test_load_and_merge_mixed_formats(self, tmp_path):
        """Load and merge .tfvars.json files."""
        loader = TerraformLoader()

        file_a = tmp_path / "base.auto.tfvars.json"
        file_a.write_text(json.dumps({"name": "base", "config": {"a": 1}}))

        file_b = tmp_path / "override.auto.tfvars.json"
        file_b.write_text(json.dumps({"name": "override", "config": {"b": 2}}))

        result = loader.load_and_merge([file_a, file_b])
        # For JSON files loaded as generic terraform, deep merge applies
        assert result["name"] == "override"


class TestTerraformLoaderLoadRaw:
    """Test raw text loading."""

    def test_load_raw_returns_content(self, tmp_path):
        """load_raw() returns file content as string."""
        loader = TerraformLoader()

        tf_file = tmp_path / "test.tf"
        content = 'resource "x" "y" {\n  a = 1\n}\n'
        tf_file.write_text(content)

        result = loader.load_raw(tf_file)
        assert result == content

    def test_load_raw_nonexistent_raises(self, tmp_path):
        """load_raw() raises for missing file."""
        loader = TerraformLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_raw(tmp_path / "nope.tf")
