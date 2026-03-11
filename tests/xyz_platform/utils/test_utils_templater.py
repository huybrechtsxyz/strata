#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_templater.py
Author        : Vincent Huybrechts
Created       : 2026-02-06
Last Updated  : 2026-02-06
Version       : 1.0.0
Python Version: 3.12+
Description   : Template processing functionality for files with placeholders.
===============================================================================
"""

import os
import tempfile
from pathlib import Path
from xyz_platform.utils.templater import TemplateProcessor


def test_process_single_template_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir)
        template_file = template_dir / "main.template.tf"
        with open(template_file, "w", encoding="utf-8") as f:
            f.write('organization = "$ORG"\nproject = "${PROJECT}"')
        monkeypatch.setenv("ORG", "my-org")
        monkeypatch.setenv("PROJECT", "my-project")
        processor = TemplateProcessor(template_dir, cleanup_templates=False)
        assert processor.process_single_template(template_file)
        output_file = template_dir / "main.tf"
        assert output_file.exists()
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert 'organization = "my-org"' in content
        assert 'project = "my-project"' in content


def test_process_all_templates_and_cleanup(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir)
        template_file = template_dir / "vars.template.tf"
        with open(template_file, "w", encoding="utf-8") as f:
            f.write('region = "$REGION"')
        monkeypatch.setenv("REGION", "eu-west-1")
        processor = TemplateProcessor(template_dir, cleanup_templates=True)
        assert processor.process_all_templates()
        output_file = template_dir / "vars.tf"
        assert output_file.exists()
        # Template file should be deleted
        assert not template_file.exists()
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert 'region = "eu-west-1"' in content


def test_process_template_missing_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir)
        template_file = template_dir / "missing.template.tf"
        with open(template_file, "w", encoding="utf-8") as f:
            f.write('foo = "$NOT_SET"')
        # Do not set NOT_SET
        processor = TemplateProcessor(template_dir, cleanup_templates=False)
        assert processor.process_single_template(template_file)
        output_file = template_dir / "missing.tf"
        assert output_file.exists()
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
        # Placeholder should remain
        assert 'foo = "$NOT_SET"' in content
