#!/usr/bin/env python3
"""Pydantic models for solution file validation.

Solution models intentionally extend ``BaseModel`` instead of ``PlatformBaseModel``.
The solution file (``.strata/solution.json``) is a CLI-managed state file — not a
user-authored YAML document — so ``extra="forbid"`` would break forward compatibility
when older CLIs encounter fields added by newer versions.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from strata.models.common_models import PlatformName


class SolutionSpecProfileConfigModel(BaseModel):
    """
    Model for configuration paths in the solution specification profiles.
    """

    name: PlatformName = Field(..., description="Name of the configuration path")
    path: str = Field(..., description="Path to the configuration file")
    type: Optional[str] = Field(
        None,
        description="Type of the configuration (e.g., config, dotenv, data, secret)",
    )
    created: Optional[str] = Field(None, description="Creation timestamp of the configuration path")


class SolutionSpecProfileModel(BaseModel):
    """
    Model for profiles in the solution specification.
    """

    name: PlatformName = Field(..., description="Name of the profile")
    active: bool = Field(..., description="Whether the profile is active")
    created: Optional[str] = Field(None, description="Creation timestamp of the profile")
    configfile_paths: Optional[List[SolutionSpecProfileConfigModel]] = Field(
        None, description="List of configuration paths associated with the profile"
    )
    envfile_paths: Optional[List[SolutionSpecProfileConfigModel]] = Field(
        None, description="List of dotenv/env-file paths associated with the profile"
    )
    datafile_paths: Optional[List[SolutionSpecProfileConfigModel]] = Field(
        None, description="List of data-file paths associated with the profile"
    )
    secretfile_paths: Optional[List[SolutionSpecProfileConfigModel]] = Field(
        None, description="List of secret-file paths associated with the profile"
    )


class SolutionSpecRepositoryModel(BaseModel):
    """
    Model for a repository entry in the solution specification.
    """

    name: PlatformName = Field(..., description="Name of the repository")
    url: str = Field(..., description="URL of the repository")
    path: str = Field(..., description="Local path to the repository")
    type: str = Field(..., description="Type of the repository")
    branch: str = Field(..., description="Branch of the repository")
    created: Optional[str] = Field(None, description="Creation timestamp of the repository")


class SolutionTemplateBundleEntryModel(BaseModel):
    """A single entry in a solution template bundle."""

    name: str = Field(..., description="Template source name to look up via the standard resolution chain")
    path: str = Field(..., description="Jinja2 destination path relative to the work path")


class SolutionTemplateModel(BaseModel):
    """A named template defined in solution.json that expands to one or more destination paths."""

    name: str = Field(..., description="Template name matched by --template")
    bundle: List["SolutionTemplateBundleEntryModel"] = Field(
        ..., description="One or more bundle entries describing source templates and their destination paths"
    )


class SolutionSpecDeploymentModel(BaseModel):
    """
    Model for a registered deployment file entry in the solution specification.

    Deployment files are registered via ``strata sln deployment add`` so that
    commands like ``strata promote`` can enumerate all deployments without
    requiring an explicit ``-f`` flag on every invocation.
    """

    name: str = Field(..., description="Deployment name (from meta.name in the deployment YAML)")
    path: str = Field(..., description="Path to the deployment YAML file (relative to work-path or absolute)")
    created: Optional[str] = Field(None, description="Registration timestamp (ISO 8601)")


class SolutionSpecModel(BaseModel):
    """
    Specification model for solution resource types.
    """

    solution_id: str = Field(..., description="Unique identifier for the solution")
    repositories: Optional[List[SolutionSpecRepositoryModel]] = Field(
        None, description="List of repositories associated with the solution"
    )
    deployments: Optional[List[SolutionSpecDeploymentModel]] = Field(
        None, description="List of deployment files registered in this solution"
    )
    profiles: Optional[List[SolutionSpecProfileModel]] = Field(
        None, description="List of profiles associated with the solution"
    )
    context: Optional[Dict[str, str]] = Field(
        None,
        description="Team-shared template substitution variables (committed to solution.json).",
    )
    templates: Optional[List[SolutionTemplateModel]] = Field(
        None,
        description="Named solution templates that scaffold one or more paths from a bundle definition.",
    )


class SolutionMetaModel(BaseModel):
    """
    Metadata model for solution resource types.
    """

    name: str = Field(..., description="Name of the solution")
    annotations: Optional[Dict[str, Any]] = Field(None, description="Annotations for the model")
    labels: Optional[Dict[str, Any]] = Field(None, description="Key-value pairs for labeling the model")
    tags: Optional[List[Any]] = Field(None, description="List of tags associated with the model")

    @field_validator("name")
    @classmethod
    def validate_name_non_empty(cls, v: str) -> str:
        """Ensure name is not empty."""
        if not v or not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()


class SolutionModel(BaseModel):
    """
    Model for solution resource types.

    A solution groups a collection of related repositories together and provides
    the source of truth for ``strata init <name>``, including the VS Code workspace
    definition, shared profiles, and per-repo metadata.
    """

    apiVersion: str = Field(..., description="API version of the solution model")
    kind: str = Field(..., description="Kind of the solution model")
    meta: SolutionMetaModel = Field(..., description="Metadata for the solution")
    spec: SolutionSpecModel = Field(..., description="Specification for the solution")

    @field_validator("apiVersion", "kind")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        """Ensure apiVersion and kind are not empty."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
