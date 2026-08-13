"""Command to render a diagram definition to Mermaid source."""

from pathlib import Path
from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.diagram_controller import DiagramController
from strata.models.diagram_model import DiagramSourceType
from strata.services.configuration_service import ConfigurationService


class ShowDiagramCommand(BaseCommand):
    """Render a ``kind: diagram`` definition, by name or by path."""

    OPERATION = "diagram_show"

    def __init__(
        self,
        file: str,
        entry: Optional[str] = None,
        save: Optional[str] = None,
        print_template: bool = False,
        no_validate: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file
        self._entry = entry
        self._save = save
        self._print_template = print_template
        self._no_validate = no_validate
        self._content: Optional[str] = None

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # Works without an initialized workspace — built-ins ship with the package.
        return self._initialize_session(show_header=show_header)

    def _execute(self) -> bool:
        controller = DiagramController(
            work_path=self._work_path,
            entry=self._entry,
            no_validate=self._no_validate,
        )

        definition_path = controller.resolve_definition(self._file)
        if definition_path is None:
            self._errors.extend(controller.get_errors())
            return False

        model = controller.load(definition_path)
        if model is None:
            self._errors.extend(controller.get_errors())
            return False

        configuration_service = None
        needs_policies = any(s.type == DiagramSourceType.POLICIES for s in model.spec.sources or [])
        if needs_policies and not self._print_template:
            # Every other source loads from a bare path — only 'policies' needs
            # an active profile's configuration, so it's the one case this
            # command loads it. --print-template emits the template text only,
            # with no context resolved, so it never needs this either.
            configuration_service = self._load_configuration_service_for_policies()
            if configuration_service is None:
                return False

        content = (
            controller.get_template(model)
            if self._print_template
            else controller.render(model, configuration_service=configuration_service)
        )
        if content is None:
            self._errors.extend(controller.get_errors())
            return False

        self._content = content
        self._output_data = {
            "diagram": model.meta.name,
            "definition": str(definition_path),
            "template" if self._print_template else "mermaid": content,
        }

        if self._save:
            self._save_content(content)
        return True

    def _after_execute(self) -> bool:
        if self._content is not None and self._is_console_output() and not self._output_quiet:
            click.echo(self._content)
        return super()._after_execute()

    def _save_content(self, content: str) -> None:
        """Write the rendered Mermaid (or template) verbatim so it can be piped onward."""
        save_path = Path(self._save) if self._save else self._work_path / "diagram.mmd"
        if not save_path.is_absolute():
            save_path = self._work_path / save_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(content, encoding="utf-8")
        self._output_data["saved_to"] = str(save_path)
        self._messages.append(f"Saved to: {save_path}")

    def _load_configuration_service_for_policies(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths.

        Mirrors ListPolicyCommand._load_configuration_service() — 'policies' is
        the one diagram source with the same active-profile dependency that
        command already has.
        """
        from strata.utils.system import resolve_path

        if self._solution_controller.solution is None:
            self._errors.append(
                "Diagram source 'policies' requires an initialized workspace. Run 'strata sln init' first."
            )
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._errors.append(
                "Diagram source 'policies' requires an active profile. Run 'strata profile activate <name>' first."
            )
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._errors.append(
                "Diagram source 'policies' requires at least one configfile path on the active profile. "
                "Add one with 'strata ref configfile add'."
            )
            return None

        repo_map = self._solution_controller.get_repo_map()

        resolved_paths = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except ValueError as exc:
                self.logger.debug("Config source skipped", name=str(entry.name), reason=str(exc))
                continue
            if not resolved.exists():
                self.logger.debug("Config source not found", name=str(entry.name), path=str(resolved))
                continue
            resolved_paths.append(str(resolved))

        if not resolved_paths:
            self._errors.append("No configfile_paths resolved to existing files. Check your profile refs.")
            return None

        try:
            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._errors.append(f"Failed to load configuration: {'; '.join(load_errors)}")
                return None
            return config_svc
        except Exception as exc:
            self._errors.append(f"Failed to load configuration service: {exc}")
            return None
