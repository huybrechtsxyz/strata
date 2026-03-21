#!/usr/bin/env python3
"""
===============================================================================
Script Name   : terraform_deployer.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Terraform deployer — step-based provisioner style.

                Supported steps (in execution order):
                  setup    — terraform init
                  check    — terraform validate
                  plan     — terraform plan  -out=<stage>.tfplan
                  apply    — terraform apply <stage>.tfplan
                  destroy  — terraform destroy  (requires --force / auto_approve)
                  output   — terraform output -json → {name: value} dict

                Typical caller sequences:
                  dry-run  : setup → check → plan
                  deploy   : setup → check → plan → apply
                  destroy  : setup → destroy  (--force required)
                  output   : output

                Working-directory resolution (priority order):
                  1. stage.provisioner (name) → workspace.spec.provisioners[name]
                  2. stage.topology (name)    → topology.provisioner type
                                              → workspace.spec.provisioners[.provisioner==type]
                  3. Single provisioner in workspace → used unconditionally

                  working_dir = deployment_build_path / iac_model.source.target_path
                  plan_file   = working_dir / "<stage.name>.tfplan"

                TODO (rollback): on_failure=rollback is not yet implemented.
===============================================================================
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from xyz_platform.controllers.value_controller import ResolvedValues, inject_tf_vars
from xyz_platform.deployers.base_deployer import (
    BaseDeployer,
    STEP_SETUP,
    STEP_CHECK,
    STEP_PLAN,
    STEP_APPLY,
    STEP_DESTROY,
    STEP_OUTPUT,
)
from xyz_platform.integrations.terraform import TerraformIntegration
from xyz_platform.logger import get_logger
from xyz_platform.models.common_models import ProvisionerType
from xyz_platform.models.integration_model import IntegrationModel

if TYPE_CHECKING:
    from xyz_platform.models.deployment_model import DeploymentStageModel
    from xyz_platform.models.workspace_model import WorkspaceIacModel
    from xyz_platform.services.configuration_service import ConfigurationService
    from xyz_platform.services.deployment_service import DeploymentService


logger = get_logger(__name__)


class TerraformDeployer(BaseDeployer):
    """Terraform deployer — runs discrete IaC steps against the build artefacts.

    Instantiate once per stage; the constructor resolves all paths and the
    TerraformIntegration instance so step methods stay free of parameters.
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
        resolved_values: Optional[ResolvedValues] = None,
    ):
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
        )

        self._resolved_values: Optional[ResolvedValues] = resolved_values

        # Resolve IaC model + paths once so all step methods can use them directly
        workspace_service = deployment_service.get_workspace_service()
        self._iac_model: Optional["WorkspaceIacModel"] = (
            self._resolve_iac_model(stage, workspace_service)
            if workspace_service
            else None
        )
        self._working_dir: Optional[Path] = (
            self._build_working_dir() if self._iac_model else None
        )
        """
        ===============================================================================
        Script Name   : terraform_deployer.py
        Author        : XYZ Platform Team
        Version       : 1.0.0
        Python Version: 3.12+
        Description   : Terraform deployer — step-based provisioner style.

                        Supported steps (in execution order):
                          setup    — terraform init
                          check    — terraform validate
                          plan     — terraform plan  -out=<stage>.tfplan
                          apply    — terraform apply <stage>.tfplan
                          destroy  — terraform destroy  (requires --force / auto_approve)
                          output   — terraform output -json → {name: value} dict

                        Typical caller sequences:
                          dry-run  : setup → check → plan
                          deploy   : setup → check → plan → apply
                          destroy  : setup → destroy  (--force required)
                          output   : output

                        Working-directory resolution (priority order):
                          1. stage.provisioner (name) → workspace.spec.provisioners[name]
                          2. stage.topology (name)    → topology.provisioner type
                                                      → workspace.spec.provisioners[.provisioner==type]
                          3. Single provisioner in workspace → used unconditionally

                          working_dir = deployment_build_path / iac_model.source.target_path
                          plan_file   = working_dir / "<stage.name>.tfplan"

                        TODO (rollback): on_failure=rollback is not yet implemented.
        ===============================================================================
        """

        from pathlib import Path
        from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

        from xyz_platform.controllers.value_controller import (
            ResolvedValues,
            inject_tf_vars,
        )
        from xyz_platform.deployers.base_deployer import (
            BaseDeployer,
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_OUTPUT,
        )
        from xyz_platform.integrations.terraform import TerraformIntegration
        from xyz_platform.logger import get_logger
        from xyz_platform.models.common_models import ProvisionerType
        from xyz_platform.models.integration_model import IntegrationModel

        if TYPE_CHECKING:
            from xyz_platform.models.deployment_model import DeploymentStageModel
            from xyz_platform.models.workspace_model import WorkspaceIacModel
            from xyz_platform.services.configuration_service import ConfigurationService
            from xyz_platform.services.deployment_service import DeploymentService

        logger = get_logger(__name__)

        class TerraformDeployer(BaseDeployer):
            """Terraform deployer — runs discrete IaC steps against the build artefacts.

            Instantiate once per stage; the constructor resolves all paths and the
            TerraformIntegration instance so step methods stay free of parameters.
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
                resolved_values: Optional[ResolvedValues] = None,
            ):
                super().__init__(
                    stage=stage,
                    deployment_service=deployment_service,
                    configuration_service=configuration_service,
                    build_path=build_path,
                    work_path=work_path,
                    verbose=verbose,
                    force=force,
                )

                self._resolved_values: Optional[ResolvedValues] = resolved_values

                # Resolve IaC model + paths once so all step methods can use them directly
                workspace_service = deployment_service.get_workspace_service()
                self._iac_model: Optional["WorkspaceIacModel"] = (
                    self._resolve_iac_model(stage, workspace_service)
                    if workspace_service
                    else None
                )
                self._working_dir: Optional[Path] = (
                    self._build_working_dir() if self._iac_model else None
                )
                self._plan_file: Optional[Path] = (
                    self._working_dir / f"{stage.name}.tfplan"
                    if self._working_dir
                    else None
                )
                self._backend_config: Optional[Dict[str, str]] = (
                    self._extract_backend_config() if self._iac_model else None
                )
                self._tf: Optional[TerraformIntegration] = (
                    TerraformDeployer._make_tf_integration(self._iac_model.name)
                    if self._iac_model
                    else None
                )

            # ------------------------------------------------------------------
            # Metadata
            # ------------------------------------------------------------------

            def get_deployer_name(self) -> str:
                return ProvisionerType.TERRAFORM

            def get_supported_steps(self) -> List[str]:
                return [
                    STEP_SETUP,
                    STEP_CHECK,
                    STEP_PLAN,
                    STEP_APPLY,
                    STEP_DESTROY,
                    STEP_OUTPUT,
                ]

            # ------------------------------------------------------------------
            # Validation
            # ------------------------------------------------------------------

            def validate_workspace(self) -> Tuple[bool, List[str]]:
                """Verify the terraform working directory and *.tf files exist."""
                messages: List[str] = []

                if not self._iac_model:
                    messages.append(
                        f"Stage '{self.stage.name}': cannot resolve a terraform provisioner. "
                        "Set stage.provisioner (by name) or stage.topology referencing a "
                        "workspace topology with provisioner='terraform'."
                    )
                    return False, messages

                if not self._working_dir or not self._working_dir.exists():
                    messages.append(
                        f"Terraform working directory does not exist: {self._working_dir}\n"
                        "  Run 'xyz build run' first to copy IaC artefacts to the build folder."
                    )
                    return False, messages

                tf_files = list(self._working_dir.glob("*.tf"))
                if not tf_files:
                    messages.append(
                        f"No *.tf files found in: {self._working_dir}\n"
                        "  The build step should have copied Terraform source code here."
                    )
                    return False, messages

                if self.verbose:
                    messages.append(
                        f"Workspace OK: {self._working_dir} ({len(tf_files)} .tf file(s))"
                    )

                return True, messages

            def validate_environment(self) -> Tuple[bool, List[str]]:
                """Verify the terraform binary is available on PATH."""
                messages: List[str] = []

                if not self._tf:
                    messages.append("TerraformIntegration could not be created.")
                    return False, messages

                available, error = self._tf.ensure_available()
                if not available:
                    messages.append(f"terraform binary is not available: {error}")
                    return False, messages

                if self.verbose:
                    messages.append("terraform binary found.")

                return True, messages

            # ------------------------------------------------------------------
            # Step methods
            # ------------------------------------------------------------------

            def setup(self) -> Tuple[bool, List[str]]:
                """terraform init — initialise backend and providers."""
                messages: List[str] = []
                messages.append(f"terraform init  ({self._working_dir})")
                try:
                    with inject_tf_vars(self._resolved_values):
                        result = self._tf.init(
                            str(self._working_dir),
                            backend_config=self._backend_config or None,
                            reconfigure=bool(self._backend_config),
                        )
                    if result.returncode != 0:
                        messages.append(f"terraform init failed:\n{result.stderr}")
                        return False, messages
                    if self.verbose and result.stdout.strip():
                        messages.append(result.stdout.strip())
                except RuntimeError as exc:
                    messages.append(f"terraform init error: {exc}")
                    return False, messages
                return True, messages

            def check(self) -> Tuple[bool, List[str]]:
                """terraform validate — syntax and consistency check."""
                messages: List[str] = []
                messages.append("terraform validate")
                try:
                    with inject_tf_vars(self._resolved_values):
                        result = self._tf.validate(str(self._working_dir))
                    if result.returncode != 0:
                        messages.append(f"terraform validate failed:\n{result.stderr}")
                        return False, messages
                    if self.verbose and result.stdout.strip():
                        messages.append(result.stdout.strip())
                except RuntimeError as exc:
                    messages.append(f"terraform validate error: {exc}")
                    return False, messages
                return True, messages

            def plan(self) -> Tuple[bool, List[str]]:
                """terraform plan — generate and save execution plan."""
                messages: List[str] = []
                messages.append(f"terraform plan  → {self._plan_file.name}")
                try:
                    with inject_tf_vars(self._resolved_values):
                        result = self._tf.plan(
                            str(self._working_dir), out_file=str(self._plan_file)
                        )
                    if result.returncode != 0:
                        messages.append(f"terraform plan failed:\n{result.stderr}")
                        return False, messages
                    if self.verbose and result.stdout.strip():
                        messages.append(result.stdout.strip())
                except RuntimeError as exc:
                    messages.append(f"terraform plan error: {exc}")
                    return False, messages
                return True, messages

            def apply(self) -> Tuple[bool, List[str]]:
                """terraform apply — execute the saved plan (no interactive prompt)."""
                messages: List[str] = []
                messages.append(f"terraform apply  {self._plan_file.name}")
                try:
                    with inject_tf_vars(self._resolved_values):
                        result = self._tf.apply(
                            str(self._working_dir), plan_file=str(self._plan_file)
                        )
                    if result.returncode != 0:
                        messages.append(f"terraform apply failed:\n{result.stderr}")
                        return False, messages
                    if self.verbose and result.stdout.strip():
                        messages.append(result.stdout.strip())
                except RuntimeError as exc:
                    messages.append(f"terraform apply error: {exc}")
                    return False, messages
                messages.append(f"✓ Stage '{self.stage.name}' applied successfully.")
                return True, messages

            def destroy(self) -> Tuple[bool, List[str]]:
                """terraform destroy — tear down all managed infrastructure for this stage.

                Requires ``force=True`` so terraform runs non-interactively (``-auto-approve``).
                Wraps the subprocess call with :func:`inject_tf_vars` so resolved secrets and
                variable overrides are available to the Terraform provider.
                """
                messages: List[str] = []
                if not self._working_dir or not self._tf:
                    messages.append(
                        f"Stage '{self.stage.name}': working directory or terraform integration "
                        "not initialised — cannot run destroy."
                    )
                    return False, messages

                messages.append(f"terraform destroy  ({self._working_dir})")
                try:
                    with inject_tf_vars(self._resolved_values):
                        result = self._tf.destroy(
                            str(self._working_dir),
                            auto_approve=self.force,
                        )
                    if result.returncode != 0:
                        messages.append(f"terraform destroy failed:\n{result.stderr}")
                        return False, messages
                    if self.verbose and result.stdout.strip():
                        messages.append(result.stdout.strip())
                except RuntimeError as exc:
                    messages.append(f"terraform destroy error: {exc}")
                    return False, messages
                messages.append(f"✓ Stage '{self.stage.name}' destroyed successfully.")
                return True, messages

            def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
                """terraform output — retrieve infrastructure outputs as a flat value dict.

                Calls ``terraform output -json``, parses the result, and returns a
                ``{name: value}`` mapping.  The raw terraform JSON format is::

                    {"key": {"value": <actual>, "type": "string", ...}, ...}

                This method unwraps the ``value`` field for each key.
                """
                import json

                messages: List[str] = []
                if not self._working_dir or not self._tf:
                    messages.append(
                        f"Stage '{self.stage.name}': working directory or terraform integration "
                        "not initialised — cannot retrieve outputs."
                    )
                    return False, {}, messages

                messages.append("terraform output -json")
                try:
                    with inject_tf_vars(self._resolved_values):
                        result = self._tf.output(
                            str(self._working_dir), json_format=True
                        )
                    if result.returncode != 0:
                        messages.append(f"terraform output failed:\n{result.stderr}")
                        return False, {}, messages
                    raw: Dict[str, Any] = (
                        json.loads(result.stdout) if result.stdout.strip() else {}
                    )
                    resolved = {
                        k: v.get("value") for k, v in raw.items() if isinstance(v, dict)
                    }
                    return True, resolved, messages
                except (RuntimeError, json.JSONDecodeError) as exc:
                    messages.append(f"terraform output error: {exc}")
                    return False, {}, messages

            # ------------------------------------------------------------------
            # Internal helpers
            # ------------------------------------------------------------------

            def _build_working_dir(self) -> Path:
                """Return the path where terraform commands run.

                The build step copies IaC source into:
                    deployment_build_path / iac_model.source.target_path

                Falls back to ``terraform/<iac_model.name>`` when target_path is unset.
                """
                deployment_build_path = self.deployment_service.get_build_path(
                    self.build_path
                )
                target = (
                    self._iac_model.source.target_path
                    or f"terraform/{self._iac_model.name}"
                )
                return deployment_build_path / target

            def _extract_backend_config(self) -> Optional[Dict[str, str]]:
                """Return backend key-value pairs from the IaC model, or None."""
                if not self._iac_model.backend:
                    return None
                config = self._iac_model.backend.configuration or {}
                # Values may contain ${var:...} / ${secret:...} references — passed through as-is.
                return {k: str(v) for k, v in config.items()} if config else None

            @staticmethod
            def _make_tf_integration(name: str) -> TerraformIntegration:
                """Return a TerraformIntegration singleton keyed by *name*."""
                return TerraformIntegration(
                    config=IntegrationModel(name=name, type="terraform")
                )

            @staticmethod
            def _resolve_iac_model(
                stage: "DeploymentStageModel",
                workspace_service,
            ) -> Optional["WorkspaceIacModel"]:
                """Resolve the WorkspaceIacModel for a stage.

                Priority:
                1. stage.provisioner set → match workspace.spec.provisioners by name
                2. stage.topology set   → find topology → get ProvisionerType
                                         → match workspace.spec.provisioners by .provisioner
                3. Single provisioner in workspace → use it unconditionally
                """
                spec = workspace_service.model.spec
                provisioners = spec.provisioners or []

                if not provisioners:
                    return None

                # Priority 1: explicit provisioner name on the stage
                if stage.provisioner:
                    match = next(
                        (p for p in provisioners if p.name == stage.provisioner), None
                    )
                    if match:
                        return match
                    logger.warning(
                        "stage.provisioner name not found in workspace.spec.provisioners",
                        extra={"stage": stage.name, "provisioner": stage.provisioner},
                    )

                # Priority 2: resolve via topology
                if stage.topology:
                    topo = next(
                        (t for t in (spec.topology or []) if t.name == stage.topology),
                        None,
                    )
                    if topo:
                        match = next(
                            (
                                p
                                for p in provisioners
                                if p.provisioner == topo.provisioner
                            ),
                            None,
                        )
                        if match:
                            return match
                        logger.warning(
                            "No workspace provisioner matches topology.provisioner type",
                            extra={
                                "stage": stage.name,
                                "topology": stage.topology,
                                "provisioner_type": str(topo.provisioner),
                            },
                        )

                # Priority 3: single provisioner — use it directly
                if len(provisioners) == 1:
                    logger.debug(
                        "Using sole workspace provisioner for stage",
                        extra={
                            "stage": stage.name,
                            "provisioner": provisioners[0].name,
                        },
                    )
                    return provisioners[0]

                return None


class TerraformDeployer(BaseDeployer):
    """Runs a deployment stage using Terraform (init → validate → plan → apply).

    A single instance can be reused across stages within the same deploy run.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    # ------------------------------------------------------------------
    # BaseDeployer interface
    # ------------------------------------------------------------------

    def before_deploy(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        build_path: Path,
        work_path: Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Verify the terraform working directory and var files exist."""
        messages: List[str] = []

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        iac_model = self._resolve_iac_model(stage, workspace_service)
        if not iac_model:
            messages.append(
                f"Stage '{stage.name}': cannot resolve a terraform provisioner. "
                "Set stage.provisioner (by name) or stage.topology with a workspace "
                "topology that has provisioner='terraform'."
            )
            return False, messages

        working_dir = self._get_working_dir(deployment_service, build_path, iac_model)

        if not working_dir.exists():
            messages.append(
                f"Terraform working directory does not exist: {working_dir}\n"
                "  Run 'xyz build run' first to copy IaC artefacts to the build folder."
            )
            return False, messages

        # Check that *.tf files are present
        tf_files = list(working_dir.glob("*.tf"))
        if not tf_files:
            messages.append(
                f"No *.tf files found in: {working_dir}\n"
                "  The build step should have copied Terraform source code here."
            )
            return False, messages

        if self.verbose:
            messages.append(
                f"Terraform working directory OK: {working_dir} "
                f"({len(tf_files)} .tf file(s))"
            )

        return True, messages

    def deploy(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Run terraform init → validate → plan [→ apply].

        When *dry_run* is True the apply step is skipped and all output is
        prefixed with ``[DRY-RUN]``.
        """
        messages: List[str] = []

        workspace_service = deployment_service.get_workspace_service()
        iac_model = self._resolve_iac_model(stage, workspace_service)
        working_dir = self._get_working_dir(deployment_service, build_path, iac_model)
        plan_file = working_dir / f"{stage.name}.tfplan"
        backend_config = self._build_backend_config(iac_model)

        tf = self._get_terraform_integration(iac_model.name)

        # ------------------------------------------------------------------
        # Step 1: init
        # ------------------------------------------------------------------
        prefix = "[DRY-RUN] " if dry_run else ""
        messages.append(f"{prefix}terraform init  ({working_dir})")
        if dry_run:
            messages.append(
                "[DRY-RUN] Would run: terraform init"
                + (" -reconfigure" if backend_config else "")
            )
        else:
            try:
                result = tf.init(
                    str(working_dir),
                    backend_config=backend_config or None,
                    reconfigure=bool(backend_config),
                )
                if result.returncode != 0:
                    messages.append(f"terraform init failed:\n{result.stderr}")
                    return False, messages
                if self.verbose:
                    messages.append(result.stdout.strip())
            except RuntimeError as exc:
                messages.append(f"terraform init error: {exc}")
                return False, messages

        # ------------------------------------------------------------------
        # Step 2: validate
        # ------------------------------------------------------------------
        messages.append(f"{prefix}terraform validate")
        if dry_run:
            messages.append("[DRY-RUN] Would run: terraform validate")
        else:
            try:
                result = tf.validate(str(working_dir))
                if result.returncode != 0:
                    messages.append(f"terraform validate failed:\n{result.stderr}")
                    return False, messages
                if self.verbose:
                    messages.append(result.stdout.strip())
            except RuntimeError as exc:
                messages.append(f"terraform validate error: {exc}")
                return False, messages

        # ------------------------------------------------------------------
        # Step 3: plan
        # ------------------------------------------------------------------
        messages.append(f"{prefix}terraform plan  → {plan_file.name}")
        if dry_run:
            messages.append(
                f"[DRY-RUN] Would run: terraform plan -out={plan_file.name}"
            )
        else:
            try:
                result = tf.plan(str(working_dir), out_file=str(plan_file))
                if result.returncode != 0:
                    messages.append(f"terraform plan failed:\n{result.stderr}")
                    return False, messages
                if self.verbose:
                    messages.append(result.stdout.strip())
            except RuntimeError as exc:
                messages.append(f"terraform plan error: {exc}")
                return False, messages

        if dry_run:
            messages.append(
                f"[DRY-RUN] Stage '{stage.name}' plan complete — apply skipped."
            )
            return True, messages

        # ------------------------------------------------------------------
        # Step 4: apply (uses saved plan file — no interactive prompt needed)
        # ------------------------------------------------------------------
        messages.append(f"terraform apply  {plan_file.name}")
        try:
            result = tf.apply(str(working_dir), plan_file=str(plan_file))
            if result.returncode != 0:
                messages.append(f"terraform apply failed:\n{result.stderr}")
                return False, messages
            if self.verbose:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform apply error: {exc}")
            return False, messages

        messages.append(f"✓ Stage '{stage.name}' applied successfully.")
        return True, messages

    def after_deploy(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        build_path: Path,
        work_path: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Post-deploy verification — confirm plan file was written (or skipped in dry-run)."""
        messages: List[str] = []

        if dry_run:
            if self.verbose:
                messages.append("[DRY-RUN] Skipping post-deploy verification.")
            return True, messages

        workspace_service = deployment_service.get_workspace_service()
        iac_model = self._resolve_iac_model(stage, workspace_service)
        working_dir = self._get_working_dir(deployment_service, build_path, iac_model)
        plan_file = working_dir / f"{stage.name}.tfplan"

        if not plan_file.exists():
            messages.append(f"Expected plan file not found after apply: {plan_file}")
            # Not blocking — apply may have consumed and removed the plan file

        if self.verbose:
            messages.append(f"Post-deploy check passed for stage '{stage.name}'.")

        return True, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_iac_model(
        self,
        stage: "DeploymentStageModel",
        workspace_service,
    ) -> Optional["WorkspaceIacModel"]:
        """Resolve the WorkspaceIacModel for a stage.

        Priority:
        1. stage.provisioner set → match workspace.spec.provisioners by name
        2. stage.topology set   → find topology → get its ProvisionerType
                                 → match workspace.spec.provisioners by .provisioner type
        3. Single provisioner workspace → use it unconditionally
        """
        spec = workspace_service.model.spec
        provisioners = spec.provisioners or []

        if not provisioners:
            return None

        # Priority 1: explicit provisioner name on stage
        if stage.provisioner:
            match = next((p for p in provisioners if p.name == stage.provisioner), None)
            if match:
                return match
            logger.warning(
                "stage.provisioner name not found in workspace.spec.provisioners",
                extra={"stage": stage.name, "provisioner": stage.provisioner},
            )

        # Priority 2: resolve via topology
        if stage.topology:
            topo = next(
                (t for t in (spec.topology or []) if t.name == stage.topology),
                None,
            )
            if topo:
                match = next(
                    (p for p in provisioners if p.provisioner == topo.provisioner),
                    None,
                )
                if match:
                    return match
                logger.warning(
                    "No workspace provisioner matches topology.provisioner type",
                    extra={
                        "stage": stage.name,
                        "topology": stage.topology,
                        "provisioner_type": str(topo.provisioner),
                    },
                )

        # Priority 3: single provisioner — use it directly
        if len(provisioners) == 1:
            logger.debug(
                "Using sole workspace provisioner for stage (no explicit reference)",
                extra={"stage": stage.name, "provisioner": provisioners[0].name},
            )
            return provisioners[0]

        return None

    def _get_working_dir(
        self,
        deployment_service: "DeploymentService",
        build_path: Path,
        iac_model: "WorkspaceIacModel",
    ) -> Path:
        """Return the filesystem path where terraform commands should run.

        The build step copies IaC source from the fetched repository to:
            deployment_build_path / iac_model.source.target_path

        If target_path is not set on the source model we fall back to
        ``terraform/<iac_model.name>`` as a safe default.
        """
        deployment_build_path = deployment_service.get_build_path(build_path)
        target = iac_model.source.target_path or f"terraform/{iac_model.name}"
        return deployment_build_path / target

    def _build_backend_config(
        self, iac_model: "WorkspaceIacModel"
    ) -> Optional[Dict[str, str]]:
        """Extract backend configuration key-value pairs from the IaC model.

        Returns None when no backend is configured.
        """
        if not iac_model.backend:
            return None

        config = iac_model.backend.configuration or {}
        # configuration values may contain ${var:...} / ${secret:...} references;
        # for now pass them through as-is — secret resolution is out of scope here.
        return {k: str(v) for k, v in config.items()} if config else None

    @staticmethod
    def _get_terraform_integration(name: str) -> TerraformIntegration:
        """Return a TerraformIntegration singleton keyed by *name*."""
        config = IntegrationModel(name=name, type="terraform")
        return TerraformIntegration(config=config)
