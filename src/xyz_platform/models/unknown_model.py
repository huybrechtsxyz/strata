#!/usr/bin/env python3
"""Generic model for unknown or unsupported resource types.

Used for forward compatibility, debugging malformed configurations,
or parsing YAML that doesn't match any known PlatformKind.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Optional, Any


class UnknownMetaModel(BaseModel):
    """
    Metadata model for unknown resource types.

    Provides flexible structure for resource identification and categorization
    when the resource type is not recognized by the platform.
    """

    name: str = Field(..., description="Name of the unknown resource")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Annotations for the model"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Key-value pairs for labeling the model"
    )
    tags: Optional[List[Any]] = Field(
        None, description="List of tags associated with the model"
    )

    @field_validator("name")
    @classmethod
    def validate_name_non_empty(cls, v: str) -> str:
        """Ensure name is not empty."""
        if not v or not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()


class UnknownModel(BaseModel):
    """
    Generic model for unknown or unsupported resource types.

    Used when parsing YAML that doesn't match any known PlatformKind.
    Provides minimal validation while preserving the structure for
    debugging, logging, or forward compatibility with future versions.

    This model intentionally keeps validation minimal to allow maximum
    flexibility for unrecognized resource formats.
    """

    apiVersion: str = Field(..., description="API version of the unknown model")
    kind: str = Field(..., description="Kind of the unknown model")
    meta: UnknownMetaModel = Field(..., description="Metadata for the unknown model")
    spec: Any = Field(..., description="Specification can be any structure")

    @field_validator("apiVersion", "kind")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        """Ensure apiVersion and kind are not empty."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
