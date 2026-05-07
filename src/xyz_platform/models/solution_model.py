#!/usr/bin/env python3
"""Pydantic models for solution file validation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from xyz_platform.models.common_models import PlatformName


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


class SolutionSpecModel(BaseModel):
    """
    Specification model for solution resource types.
    """

    solution_id: str = Field(..., description="Unique identifier for the solution")
    repositories: Optional[List[SolutionSpecRepositoryModel]] = Field(
        None, description="List of repositories associated with the solution"
    )
    profiles: Optional[List[SolutionSpecProfileModel]] = Field(
        None, description="List of profiles associated with the solution"
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
    the source of truth for ``xyz init <name>``, including the VS Code workspace
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
