"""Workflow model for strata console onboarding steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import yaml

CheckStatus = Literal["ok", "warn", "pending"]


@dataclass
class CheckResult:
    """Result of running a workflow step's check function."""

    status: CheckStatus
    detail: Optional[str] = None


@dataclass
class WorkflowStep:
    """A single step in the workspace onboarding workflow."""

    id: str
    name: str
    check: str  # Name of the check function to run (looked up in WORKFLOW_CHECKS)
    command: Optional[str] = None  # strata command to execute (None = dynamic)
    depends_on: list[str] = field(default_factory=list)  # Step IDs that must complete first
    hint: str = ""  # Guidance text shown by `next`
    see_also: Optional[str] = None  # Reference link or help topic
    skippable: bool = False  # Can the user skip this step?

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for YAML serialization."""
        result = {
            "id": self.id,
            "name": self.name,
            "check": self.check,
            "depends_on": self.depends_on if self.depends_on else [],
            "hint": self.hint,
            "skippable": self.skippable,
        }
        if self.command:
            result["command"] = self.command
        if self.see_also:
            result["see_also"] = self.see_also
        return result


@dataclass
class WorkflowDefinition:
    """Complete workflow definition — ordered list of steps."""

    steps: list[WorkflowStep] = field(default_factory=list)

    @classmethod
    def load_yaml(cls, content: str) -> WorkflowDefinition:
        """Load workflow from YAML string."""
        try:
            data = yaml.safe_load(content) or {}
            steps_data = data.get("steps", [])
            steps = [
                WorkflowStep(
                    id=step["id"],
                    name=step["name"],
                    check=step["check"],
                    command=step.get("command"),
                    depends_on=step.get("depends_on", []),
                    hint=step.get("hint", ""),
                    see_also=step.get("see_also"),
                    skippable=step.get("skippable", False),
                )
                for step in steps_data
            ]
            return cls(steps=steps)
        except (KeyError, TypeError, yaml.YAMLError) as e:
            raise ValueError(f"Invalid workflow YAML: {e}") from e

    def to_yaml(self) -> str:
        """Convert to YAML string."""
        data = {"steps": [step.to_dict() for step in self.steps]}
        return yaml.dump(data, default_flow_style=False, sort_keys=False)


def get_default_workflow() -> WorkflowDefinition:
    """Return the built-in default workflow (8 phases)."""
    return WorkflowDefinition(
        steps=[
            WorkflowStep(
                id="workspace_init",
                name="Workspace initialized",
                check="solution_exists",
                command="strata sln init {name}",
                hint="Initialize the workspace to create .strata/ and solution.json",
                see_also="strata help --topic quickstart",
            ),
            WorkflowStep(
                id="repos_registered",
                name="Repositories registered",
                check="repos_registered",
                depends_on=["workspace_init"],
                command="strata repo add {name} {url}",
                hint="Register at least one configuration repository",
                see_also="strata help --topic repos",
            ),
            WorkflowStep(
                id="repos_on_disk",
                name="Repositories on disk",
                check="repos_cloned",
                depends_on=["repos_registered"],
                command=None,  # dynamic — git clone per missing repo
                hint="Clone registered repositories to their configured paths",
                see_also="strata help --topic repos",
            ),
            WorkflowStep(
                id="profile_created",
                name="Profile created",
                check="profile_exists",
                depends_on=["workspace_init"],
                command="strata profile add {name} --activate",
                hint="Create a profile to organize file references",
                see_also="strata help --topic profiles",
            ),
            WorkflowStep(
                id="profile_activated",
                name="Profile activated",
                check="profile_active",
                depends_on=["profile_created"],
                command="strata profile activate {name}",
                hint="Activate a profile to set the working context",
                see_also="strata help --topic profiles",
            ),
            WorkflowStep(
                id="files_registered",
                name="File references registered",
                check="files_registered",
                depends_on=["profile_activated"],
                command="strata ref config add {name} @{repo}/path/to/config.yaml --profile {active}",
                hint="Register configuration files against the active profile",
                see_also="strata help --topic environments",
            ),
            WorkflowStep(
                id="build_exists",
                name="Build artifact exists",
                check="build_exists",
                command="strata build run",
                hint="Run a build to generate deployment artifacts",
                see_also="strata help --topic build",
            ),
            WorkflowStep(
                id="inventory_generated",
                name="Platform inventory generated",
                check="sbom_exists",
                depends_on=["build_exists"],
                command="strata build sbom -f {file}",
                hint="Generate the platform inventory:\n\n  strata build sbom -f <file>\n\nOr for a human-readable overview:\n\n  strata build sbom -f <file> --report inventory",
                see_also="docs/platform/builders.md",
            ),
        ]
    )
