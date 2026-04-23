#!/usr/bin/env python3
"""Pydantic models for project file validation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from xyz_platform.models.common_models import PlatformName


class ProjectSpecProfileConfigModel(BaseModel):
    """
    Model for configuration paths in the project specification profiles.
    """

    name: PlatformName = Field(..., description="Name of the configuration path")
    path: str = Field(..., description="Path to the configuration file")
    type: Optional[str] = Field(
        None,
        description="Type of the configuration (e.g., config, dotenv, data, secret)",
    )
    created: Optional[str] = Field(None, description="Creation timestamp of the configuration path")


class ProjectSpecProfileModel(BaseModel):
    """
    Model for profiles in the project specification.
    """

    name: PlatformName = Field(..., description="Name of the profile")
    active: bool = Field(..., description="Whether the profile is active")
    created: Optional[str] = Field(None, description="Creation timestamp of the profile")
    config_paths: Optional[List[ProjectSpecProfileConfigModel]] = Field(
        None, description="List of configuration paths associated with the profile"
    )
    dotenv_paths: Optional[List[ProjectSpecProfileConfigModel]] = Field(
        None, description="List of dotenv paths associated with the profile"
    )
    data_paths: Optional[List[ProjectSpecProfileConfigModel]] = Field(
        None, description="List of data paths associated with the profile"
    )
    secret_paths: Optional[List[ProjectSpecProfileConfigModel]] = Field(
        None, description="List of secret paths associated with the profile"
    )


class ProjectSpecRepositoriesModel(BaseModel):
    """
    Model for repositories in the project specification.
    """

    name: PlatformName = Field(..., description="Name of the repository")
    url: str = Field(..., description="URL of the repository")
    path: str = Field(..., description="Path to the repository")
    type: str = Field(..., description="Type of the repository")
    branch: str = Field(..., description="Branch of the repository")
    created: Optional[str] = Field(None, description="Creation timestamp of the repository")


class ProjectSpecModel(BaseModel):
    """
    Specification model for project resource types.
    """

    # Define any specific fields for the spec here, or keep it flexible
    # For example, you can add a field like:
    # config: Optional[Dict[str, Any]] = Field(None, description="Configuration for the project")
    repositories: Optional[List[ProjectSpecRepositoriesModel]] = Field(
        None, description="List of repositories associated with the project"
    )
    profiles: Optional[List[ProjectSpecProfileModel]] = Field(
        None, description="List of profiles associated with the project"
    )


class ProjectMetaModel(BaseModel):
    """
    Metadata model for project resource types.
    """

    name: str = Field(..., description="Name of the unknown resource")
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


class ProjectModel(BaseModel):
    """
    Generic model for project resource types.
    """

    apiVersion: str = Field(..., description="API version of the unknown model")
    kind: str = Field(..., description="Kind of the unknown model")
    meta: ProjectMetaModel = Field(..., description="Metadata for the unknown model")
    spec: ProjectSpecModel = Field(..., description="Specification can be any structure")

    @field_validator("apiVersion", "kind")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        """Ensure apiVersion and kind are not empty."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
