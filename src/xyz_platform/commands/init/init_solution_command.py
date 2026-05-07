"""Command to initialize a new XYZ Platform solution workspace."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.exceptions import ModelValidationError, PlatformFileNotFoundError
from xyz_platform.logger import get_logger
from xyz_platform.models.platform_template_model import PlatformTemplateModel
from xyz_platform.models.solution_model import (
    SolutionSpecProfileConfigModel,
    SolutionSpecProfileModel,
    SolutionSpecRepositoryModel,
)
from xyz_platform.services.platform_template_service import load_workspace_template


class InitSolutionCommand(BaseCommand):
    """
    Initialize a new XYZ Platform solution workspace.

    Creates the ``.platform/`` state directory, ``solution.json``, and a
    ``<name>.code-workspace`` file in the work path.

    When *from_template* is given the workspace is also pre-populated with the
    repositories, profiles, and file references declared in the template file.
    """

    OPERATION = "solution_init"
    INIT_REQUIRED = False  # Allow running even if no existing solution is detected, since this command is for initializing a new solution

    def __init__(
        self,
        name: str,
        from_template: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self.logger = get_logger(self.__class__.__module__)
        self._solution_name = name
        self._from_template: Optional[str] = from_template
        self._template: Optional[PlatformTemplateModel] = None

    # ------------------------------------------------------------------
    # BaseCommand interface
    # ------------------------------------------------------------------

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self.logger.error(f"Pre-execution validation failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            if not self._run_execution():
                self.logger.error(f"Execution failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                self.logger.error(f"Post-execution processing failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to initialize solution workspace: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _initialize(self, show_header: bool = True) -> bool:
        if not super()._initialize(show_header=show_header):
            return False
        self.logger.debug(
            "InitSolutionCommand initializing",
            extra={
                "solution_name": self._solution_name,
                "work_path": str(self._work_path),
            },
        )
        self.logger.debug(
            "InitSolutionCommand initialized successfully",
            extra={
                "solution_name": self._solution_name,
                "work_path": str(self._work_path),
            },
        )
        return True

    def _before_execute(self) -> bool:
        if not super()._before_execute():
            return False
        if self._from_template is not None:
            template_path = Path(self._from_template)
            if not template_path.is_absolute():
                template_path = self._work_path / template_path
            try:
                self._template, _ = load_workspace_template(template_path)
            except (PlatformFileNotFoundError, ModelValidationError) as exc:
                self._errors.append(str(exc.message))
                return False
        return True

    def _run_execution(self) -> bool:
        """Run the main execution logic for initializing the solution workspace."""
        ok, errors = self._solution_controller.init(self._solution_name)
        self._messages.extend(self._solution_controller.get_messages())
        self._errors.extend(errors)

        if not ok:
            self._finalize(success=False)
            return False

        if self._template is not None:
            if not self._apply_template(self._template):
                self._finalize(success=False)
                return False

        self._output_data = {
            "solution_name": self._solution_name,
            "solution_id": self._solution_controller.get_solution_id(),
            "work_path": str(self._work_path),
            "template": str(self._template.meta.name) if self._template else None,
        }

        return True

    # ------------------------------------------------------------------
    # Template application
    # ------------------------------------------------------------------

    def _apply_template(self, template: PlatformTemplateModel) -> bool:
        """Register repos and profiles declared in *template* into the new solution."""
        now = datetime.now(timezone.utc).isoformat()

        # --- Repositories ---
        for repo_tpl in template.spec.repos or []:
            local_path = repo_tpl.path if repo_tpl.path else f"repos/{repo_tpl.name}"
            repo = SolutionSpecRepositoryModel(
                name=repo_tpl.name,
                url=repo_tpl.url,
                branch=repo_tpl.branch,
                path=local_path,
                type="gitops",
                created=now,
            )
            ok, errors = self._solution_controller.add_repository(repo)
            self._errors.extend(errors)
            if not ok:
                return False
            self.logger.info("Template: registered repo", name=str(repo_tpl.name))

        # --- Profiles + refs ---
        activate_name: Optional[str] = None
        for profile_tpl in template.spec.profiles or []:
            profile = SolutionSpecProfileModel(
                name=profile_tpl.name,
                active=False,
                created=now,
                configfile_paths=[],
                envfile_paths=[],
                datafile_paths=[],
                secretfile_paths=[],
            )
            ok, errors = self._solution_controller.add_profile(profile)
            self._errors.extend(errors)
            if not ok:
                return False
            self.logger.info("Template: created profile", name=str(profile_tpl.name))

            if profile_tpl.refs:
                from xyz_platform.models.platform_template_model import PlatformTemplateRefModel as _RefModel

                ref_groups: List[tuple[str, Optional[List[_RefModel]]]] = [
                    ("configfile", profile_tpl.refs.configfile),
                    ("envfile", profile_tpl.refs.envfile),
                    ("datafile", profile_tpl.refs.datafile),
                    ("secretfile", profile_tpl.refs.secretfile),
                ]
                for ref_type, ref_list in ref_groups:
                    for ref in ref_list or []:
                        cfg = SolutionSpecProfileConfigModel(
                            name=ref.name,
                            path=ref.path,
                            type=ref_type,
                            created=now,
                        )
                        ok, errors = self._solution_controller.add_profile_path(str(profile_tpl.name), ref_type, cfg)
                        self._errors.extend(errors)
                        if not ok:
                            return False
                        self.logger.info(
                            "Template: added ref",
                            profile=str(profile_tpl.name),
                            type=ref_type,
                            name=str(ref.name),
                        )

            if profile_tpl.activate:
                activate_name = str(profile_tpl.name)

        if activate_name:
            ok, errors = self._solution_controller.activate_profile(activate_name)
            self._errors.extend(errors)
            if not ok:
                return False
            self.logger.info("Template: activated profile", name=activate_name)

        ok, errors = self._solution_controller.save()
        self._errors.extend(errors)
        return ok

    def _after_execute(self) -> bool:
        if self._is_console_output():
            click.echo(f"\n✅  Solution '{self._solution_name}' initialised")
            click.echo(f"    • Work path    : {self._work_path}")
            click.echo(f"    • Solution ID  : {self._output_data.get('solution_id', '')}")
            if self._template:
                repo_count = len(self._template.spec.repos or [])
                profile_count = len(self._template.spec.profiles or [])
                click.echo(f"    • Template     : {self._template.meta.name}")
                click.echo(f"    • Repos        : {repo_count} registered")
                click.echo(f"    • Profiles     : {profile_count} created")
            click.echo("")
        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)
