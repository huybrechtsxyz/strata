#!/usr/bin/env python3
"""Pydantic v2 model for scaffold template manifest files (template.yaml)."""

from typing import List, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel


class ScaffoldTemplateVariable(PlatformBaseModel):
    """A single substitution variable declared in a template manifest."""

    name: str = Field(..., description="Variable name, used as {{ name }} in scaffold files")
    description: str = Field("", description="Human-readable description shown on init")
    default: str = Field("", description="Default value used when no override is provided")


class ScaffoldTemplateModel(PlatformBaseModel):
    """
    Manifest for a scaffold template folder.

    Lives at ``template.yaml`` in the root of a template folder::

        name: aks
        description: Azure Kubernetes Service starter workspace
        variables:
          - name: solution_name
            description: Logical name for your solution
            default: my-workspace

    All files under ``scaffold/`` are copied into the workspace with
    ``{{ variable_name }}`` placeholders replaced by their resolved values.
    ``{{ solution_name }}`` is always available from the ``--name`` flag.
    """

    name: str = Field(..., description="Template name (matches the folder name for built-ins)")
    description: str = Field("", description="One-line description shown on init")
    variables: List[ScaffoldTemplateVariable] = Field(
        default_factory=list,
        description="Substitution variables available in scaffold files",
    )

    def get_default_variables(self) -> dict[str, str]:
        """Return {name: default} for all declared variables."""
        return {v.name: v.default for v in self.variables}

    def get_variable(self, name: str) -> Optional[ScaffoldTemplateVariable]:
        """Look up a variable by name."""
        return next((v for v in self.variables if v.name == name), None)
