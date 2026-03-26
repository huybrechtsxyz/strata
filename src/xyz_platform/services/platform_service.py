#!/usr/bin/env python3
"""
===============================================================================
Module Name   : platform_service.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Platform service for managing platform model I/O operations.
                Handles reading and writing PlatformModel artifacts in JSON and
                YAML formats.  Inherits from BaseService for consistent service
                pattern, caching, and validation.
===============================================================================
"""

import json
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.platform_model import PlatformModel
from xyz_platform.services.base_service import BaseService
from xyz_platform.services.workspace_service import WorkspaceService


class PlatformService(BaseService):
    """Service for managing platform model I/O operations."""

    def __init__(self, path=None, data=None):
        """
        Initialize the platform service.

        Args:
            path: Path to platform file (JSON or YAML). May be None when the
                  service is used for saving only (model is built externally and
                  handed to save_to_json / save_to_yaml).
            data: Optional pre-loaded data dictionary.
        """
        super().__init__(path, data)
        self.model: Optional[PlatformModel] = None
        self.verbose: bool = False
        self._workspace_service: Optional[WorkspaceService] = None

    # ------------------------------------------------------------------
    # BaseService abstract-method implementations
    # ------------------------------------------------------------------

    def _load_data(self):
        """Load data for the service.

        Overrides BaseService to allow creating service instances without
        path/data for saving purposes only (model is built by a builder and
        passed directly to save_*).
        """
        if self.path is None and self.data is None:
            self.logger.debug(
                "No path or data provided — service created for saving only"
            )
            return
        super()._load_data()

    def _get_model_class(self):
        """Return the PlatformModel class for Pydantic validation."""
        return PlatformModel

    def _validate_dynamic(
        self,
        configuration_model: Optional[ConfigurationModel] = None,
        work_path: Optional[str] = None,
        **kwargs,
    ) -> Tuple[bool, List[str]]:
        """Phase-2 dynamic validation.

        PlatformModel is an output artifact; all structural validation is
        handled by Pydantic at load time.  No cross-reference checks are
        required here.

        Returns:
            Tuple[bool, List[str]]: (True, []) — always passes.
        """
        return True, []

    def validate(
        self, configuration_model=None, work_path=None
    ) -> Tuple[bool, List[str]]:
        """Validate the platform model.

        When the service is used solely for saving (no path or data was
        provided at construction), there is nothing to validate yet.

        Args:
            configuration_model: Accepted for API compatibility (unused).
            work_path: Accepted for API compatibility (unused).

        Returns:
            Tuple[bool, List[str]]: (success, list of errors).
        """
        if self.path is None and self.data is None:
            self._validated = True
            return True, []
        return super().validate()

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def platform(self) -> Optional[PlatformModel]:
        """Return the loaded PlatformModel instance."""
        return self.model

    def get_name(self) -> str:
        """Return the platform name from the model, or 'unknown'."""
        if self.model and self.model.meta:
            return self.model.meta.name
        return "unknown"

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    def get_stages(self) -> List:
        """Return the list of deployment stages (empty list if none)."""
        if not self.model or not self.model.spec or not self.model.spec.stages:
            return []
        return self.model.spec.stages

    def get_stage_count(self) -> int:
        """Return the number of deployment stages."""
        return len(self.get_stages())

    def get_stage_by_name(self, stage_name: str):
        """Return a specific stage by name, or None if not found.

        Args:
            stage_name: Name of the stage to look up.

        Returns:
            DeploymentStageModel or None.
        """
        return next((s for s in self.get_stages() if s.name == stage_name), None)

    def has_multi_stage(self) -> bool:
        """Return True if the model contains more than one stage."""
        return self.get_stage_count() > 1

    # ------------------------------------------------------------------
    # Related-service compatibility stubs
    # ------------------------------------------------------------------

    def load_related_services(
        self, objects_path: Optional[str] = None, stage_name: Optional[str] = None
    ):
        """Compatibility stub — PlatformModel is self-contained.

        All data is embedded in the model; no external service loading is
        needed.

        Args:
            objects_path: Unused (accepted for compatibility).
            stage_name: Unused (accepted for compatibility).

        Returns:
            Tuple[dict, bool]: ({}, True).
        """
        return {}, True

    def get_workspace_service(self) -> Optional[WorkspaceService]:
        """Return a WorkspaceService built from the embedded workspace data.

        The service is created on first call and cached for subsequent calls.

        Returns:
            WorkspaceService or None if model is not loaded.
        """
        if self._workspace_service is None and self.model and self.model.spec:
            workspace_data = (
                self.model.spec.workspace.model_dump()
                if self.model.spec.workspace
                else {}
            )
            self._workspace_service = WorkspaceService(data=workspace_data)
        return self._workspace_service

    def get_environment_services(self) -> dict:
        """Compatibility stub — environments are merged into PlatformModel.

        Returns:
            dict: Empty dict (no separate environment services).
        """
        return {}

    # ------------------------------------------------------------------
    # I/O — load
    # ------------------------------------------------------------------

    def load_from_json(self, path: Path) -> PlatformModel:
        """Load a PlatformModel from a JSON file.

        Args:
            path: Path to JSON file.

        Returns:
            PlatformModel instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValidationError: If the JSON does not match PlatformModel.
        """
        if self.verbose:
            self.logger.info(f"Loading platform model from JSON: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.model = PlatformModel.model_validate(data)

        if self.verbose:
            self.logger.info(f"Loaded platform model: {self.model.meta.name}")

        return self.model

    def load_from_yaml(self, path: Path) -> PlatformModel:
        """Load a PlatformModel from a YAML file.

        Args:
            path: Path to YAML file.

        Returns:
            PlatformModel instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValidationError: If the YAML does not match PlatformModel.
        """
        if self.verbose:
            self.logger.info(f"Loading platform model from YAML: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.model = PlatformModel.model_validate(data)

        if self.verbose:
            self.logger.info(f"Loaded platform model: {self.model.meta.name}")

        return self.model

    # ------------------------------------------------------------------
    # I/O — save
    # ------------------------------------------------------------------

    def save_to_json(
        self, platform: PlatformModel, path: Path, indent: int = 2
    ) -> None:
        """Serialise a PlatformModel to a JSON file.

        Args:
            platform: PlatformModel to serialise.
            path: Destination path.
            indent: JSON indentation level (default 2).
        """
        if self.verbose:
            self.logger.info(f"Saving platform model to JSON: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            platform.model_dump_json(indent=indent, exclude_none=True),
            encoding="utf-8",
        )

        if self.verbose:
            self.logger.info(f"Saved platform model: {platform.meta.name}")

    def save_to_yaml(self, platform: PlatformModel, path: Path) -> None:
        """Serialise a PlatformModel to a YAML file.

        Args:
            platform: PlatformModel to serialise.
            path: Destination path.
        """
        if self.verbose:
            self.logger.info(f"Saving platform model to YAML: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                platform.model_dump(exclude_none=True, mode="json"),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        if self.verbose:
            self.logger.info(f"Saved platform model: {platform.meta.name}")

    def save_both_formats(
        self, platform: PlatformModel, json_path: Path, yaml_path: Path
    ) -> None:
        """Serialise a PlatformModel to both JSON and YAML.

        Args:
            platform: PlatformModel to serialise.
            json_path: Destination path for the JSON file.
            yaml_path: Destination path for the YAML file.
        """
        self.save_to_json(platform, json_path)
        self.save_to_yaml(platform, yaml_path)
