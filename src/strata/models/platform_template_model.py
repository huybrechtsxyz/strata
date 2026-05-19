#!/usr/bin/env python3
"""Pydantic v2 model for workspace template YAML files."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from strata.models.common_models import PlatformName


class PlatformTemplateRepoModel(BaseModel):
    """A single repository entry in a workspace template."""

    name: PlatformName = Field(..., description="Logical name for the repository")
    url: str = Field(..., description="Git clone URL")
    branch: str = Field("main", description="Branch to check out")
    path: Optional[str] = Field(None, description="Relative local path (defaults to repos/<name>)")


class PlatformTemplateRefModel(BaseModel):
    """A single file reference entry in a workspace template profile."""

    name: PlatformName = Field(..., description="Logical name for the reference")
    path: str = Field(..., description="File path — local path or @repo-name/... notation")


class PlatformTemplateProfileRefsModel(BaseModel):
    """File references grouped by ref type for a template profile."""

    configfile: Optional[List[PlatformTemplateRefModel]] = Field(None, description="Config file refs")
    envfile: Optional[List[PlatformTemplateRefModel]] = Field(None, description="Environment file refs")
    datafile: Optional[List[PlatformTemplateRefModel]] = Field(None, description="Data file refs")
    secretfile: Optional[List[PlatformTemplateRefModel]] = Field(None, description="Secret file refs")


class PlatformTemplateProfileModel(BaseModel):
    """A single profile entry in a workspace template."""

    name: PlatformName = Field(..., description="Profile name")
    activate: bool = Field(False, description="Whether to activate this profile after creation")
    refs: Optional[PlatformTemplateProfileRefsModel] = Field(None, description="File references for this profile")


class PlatformTemplateSpecModel(BaseModel):
    """Specification block for a workspace template."""

    repos: Optional[List[PlatformTemplateRepoModel]] = Field(None, description="Repositories to register")
    profiles: Optional[List[PlatformTemplateProfileModel]] = Field(None, description="Profiles to create")

    @model_validator(mode="after")
    def validate_single_activate(self) -> "PlatformTemplateSpecModel":
        """At most one profile may have activate: true."""
        if self.profiles:
            active = [p for p in self.profiles if p.activate]
            if len(active) > 1:
                raise ValueError(f"Only one profile may have activate: true; got: {[str(p.name) for p in active]}")
        return self


class PlatformTemplateMetaModel(BaseModel):
    """Metadata block for a workspace template."""

    name: PlatformName = Field(..., description="Template name")
    annotations: Optional[Dict[str, Any]] = Field(None, description="Free-form annotations")


class PlatformTemplateModel(BaseModel):
    """
    Top-level model for a workspace template YAML file.

    Example::

        apiVersion: platform.huybrechts.xyz/v1
        kind: workspace-template
        meta:
          name: standard-three-repo
        spec:
          repos:
            - name: xyz-config
              url: "git@github.com:org/xyz-config.git"
              branch: main
          profiles:
            - name: prd
              activate: true
              refs:
                configfile:
                  - name: global-config
                    path: "@xyz-config/config/xyz-config.yaml"
    """

    apiVersion: str = Field(..., description="API version (platform.huybrechts.xyz/v1)")
    kind: Literal["workspace-template"] = Field(..., description="Must be 'workspace-template'")
    meta: PlatformTemplateMetaModel = Field(..., description="Template metadata")
    spec: PlatformTemplateSpecModel = Field(..., description="Template specification")
