#!/usr/bin/env python3
"""Common test fixtures and utilities for xyz-platform tests."""

import os
import shutil
from pathlib import Path
from typing import Optional

import pytest


@pytest.fixture
def cli_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src"))
    return env


@pytest.fixture
def cli_path():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/xyz_platform/cli.py"))


@pytest.fixture
def build_path():
    # Always use a local path under the project: tests/build/temp/output
    base = Path(__file__).parent.parent.parent / "build" / "temp" / "output"
    path = base.absolute()
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


@pytest.fixture
def work_path():
    # Always use a local path under the project: tests/build/temp/workspace
    base = Path(__file__).parent.parent.parent / "build" / "temp" / "workspace"
    path = base.absolute()
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_data_path(path: Optional[str] = None):
    if path:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "../data", path))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))


def set_environment():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src"))
    return env


def remove_build_path():
    path = Path(__file__).parent.parent / "build" / "temp" / "output"
    path = path.absolute()
    if path.exists():
        shutil.rmtree(path)


def remove_work_path():
    path = Path(__file__).parent.parent / "build" / "temp" / "workspace"
    path = path.absolute()
    if path.exists():
        shutil.rmtree(path)
