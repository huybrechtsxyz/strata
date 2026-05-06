#!/usr/bin/env python3
"""Loader for workspace template YAML files."""

from pathlib import Path
from typing import List, Tuple

import yaml
from pydantic import ValidationError

from xyz_platform.exceptions import ModelValidationError, PlatformFileNotFoundError
from xyz_platform.logger import get_logger
from xyz_platform.models.platform_template_model import PlatformTemplateModel

logger = get_logger(__name__)


def load_workspace_template(path: Path) -> Tuple[PlatformTemplateModel, List[str]]:
    """Load and validate a workspace template YAML file.

    Args:
        path: Absolute path to the template file.

    Returns:
        ``(model, errors)`` — errors is empty on success.

    Raises:
        PlatformFileNotFoundError: If the file does not exist.
        ModelValidationError: If the YAML is structurally invalid.
    """
    if not path.exists():
        raise PlatformFileNotFoundError(file_path=str(path), file_type="template")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlatformFileNotFoundError(file_path=str(path), file_type="template") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ModelValidationError(
            model_name="WorkspaceTemplateModel",
            validation_errors=[{"msg": str(exc)}],
            message=f"Template file is not valid YAML: {exc}",
        ) from exc

    if not isinstance(data, dict):
        raise ModelValidationError(
            model_name="WorkspaceTemplateModel",
            validation_errors=[{"msg": f"expected mapping, got {type(data).__name__}"}],
            message=f"Template file must be a YAML mapping, got: {type(data).__name__}",
        )

    try:
        model = PlatformTemplateModel.model_validate(data)
    except ValidationError as exc:
        errors = [{"loc": str(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ModelValidationError(
            model_name="WorkspaceTemplateModel",
            validation_errors=errors,
            message=f"Template validation failed ({len(errors)} error(s))",
        ) from exc

    logger.debug("Workspace template loaded", name=str(model.meta.name), path=str(path))
    return model, []
