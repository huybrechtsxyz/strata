#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_deployer.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Abstract base class for IaC deployers (step-based provisioner style).

                Design mirrors the provisioner pattern:
                  - Context (stage, services, paths) is passed once to __init__
                  - Each IaC operation is a discrete named step method
                  - The caller (RunDeployCommand) chooses which steps to execute
                    based on dry_run / destroy / output flags
                  - get_supported_steps() advertises which steps are available

                Step names (canonical):
                  setup    — initialise the IaC tool (terraform init)
                  check    — validate configuration (terraform validate)
                  plan     — preview changes      (terraform plan)
                  apply    — apply changes        (terraform apply)
                  destroy  — tear down resources  (terraform destroy)
                  output   — retrieve outputs     (terraform output)
===============================================================================
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from xyz_platform.models.deployment_model import DeploymentStageModel
    from xyz_platform.services.configuration_service import ConfigurationService
    from xyz_platform.services.deployment_service import DeploymentService


# Canonical step name constants — use these in get_supported_steps() and callers.
STEP_SETUP = "setup"
STEP_CHECK = "check"
STEP_PLAN = "plan"
STEP_APPLY = "apply"
STEP_DESTROY = "destroy"
STEP_OUTPUT = "output"


class BaseDeployer(ABC):
    """Abstract base for IaC deployers.

    Instantiate once per stage:
        deployer = MyDeployer(
            stage=stage,
            deployment_service=...,
            configuration_service=...,
            build_path=...,
            work_path=...,
        )
        ok, msgs = deployer.validate_workspace()
        ok, msgs = deployer.validate_environment()
        for step in steps_to_run:
            ok, msgs = getattr(deployer, step)()
    """

    def __init__(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
    ):
        self.stage = stage
        self.deployment_service = deployment_service
        self.configuration_service = configuration_service
        self.build_path = build_path
        self.work_path = work_path
        self.verbose = verbose
        self.force = force

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @abstractmethod
    def get_deployer_name(self) -> str:
        """Return the canonical name/type of this deployer (e.g. 'terraform')."""
        raise NotImplementedError

    @abstractmethod
    def get_supported_steps(self) -> List[str]:
        """Return the ordered list of step names this deployer supports.

        Example: ["setup", "check", "plan", "apply", "destroy", "output"]
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Validation (pre-step guards)
    # ------------------------------------------------------------------

    @abstractmethod
    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the workspace artefacts required by this deployer exist.

        Called before any steps run.  Should check that IaC source files
        were copied to the build path by the build pipeline.

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify tool-specific requirements (binary on PATH, auth, etc.).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    @abstractmethod
    def setup(self) -> Tuple[bool, List[str]]:
        """Initialise the IaC tool (e.g. terraform init).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def check(self) -> Tuple[bool, List[str]]:
        """Validate IaC configuration (e.g. terraform validate).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def plan(self) -> Tuple[bool, List[str]]:
        """Preview infrastructure changes (e.g. terraform plan).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def apply(self) -> Tuple[bool, List[str]]:
        """Apply infrastructure changes (e.g. terraform apply).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def destroy(self) -> Tuple[bool, List[str]]:
        """Destroy infrastructure resources (e.g. terraform destroy).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Retrieve infrastructure outputs (e.g. terraform output).

        Returns:
            (success, outputs_dict, messages)
        """
        raise NotImplementedError
