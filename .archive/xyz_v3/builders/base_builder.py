#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_builder.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base class for deployment builders.
===============================================================================
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple

from xyz_platform.services.deployment_service import DeploymentService


class BaseBuilder(ABC):
    """Abstract base class for workspace builders."""

    @abstractmethod
    def build(
        self, deployment_service: DeploymentService, work_path: Path, build_path: Path
    ) -> Tuple[bool, List[str]]:
        """Build the workspace according to the builder's logic."""
        raise NotImplementedError

    @abstractmethod
    def before_build(
        self, deployment_service: DeploymentService, work_path: Path, build_path: Path
    ) -> Tuple[bool, List[str]]:
        """Hook executed before the build process starts."""
        raise NotImplementedError

    @abstractmethod
    def after_build(
        self, deployment_service: DeploymentService, work_path: Path, build_path: Path
    ) -> Tuple[bool, List[str]]:
        """Hook executed after the build process completes."""
        raise NotImplementedError
