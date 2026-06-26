"""Shared output path resolution helpers for deploy artifacts.

This module centralizes output-directory path construction used by both
deploy-log and deployment-manifest writers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from strata.utils.templater import TemplateProcessor


class OutputWriter:
    """Utility helpers for resolving output directories."""

    @staticmethod
    def resolve_structured_output_dir(
        base_path: Path,
        structure: str,
        path_definitions: Dict[str, str],
        builtin_path_definitions: Dict[str, str],
        context: Dict[str, str],
    ) -> Path:
        """Resolve an output directory from a named or inline template structure."""
        template = path_definitions.get(
            structure,
            builtin_path_definitions.get(structure, structure),
        )

        rendered = TemplateProcessor.render(template, context)
        segments = [segment for segment in rendered.split("/") if segment.strip()]
        if segments:
            return base_path / Path(*segments)
        return base_path

    @staticmethod
    def resolve_versioned_output_dir(
        base_path: Path,
        deployment_name: str,
        version: str | None = None,
    ) -> Path:
        """Resolve a versioned output directory for deployment manifests."""
        output_dir = base_path / deployment_name
        if version:
            output_dir = output_dir / version
        return output_dir
