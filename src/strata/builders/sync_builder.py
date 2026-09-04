"""Build sync artifacts (Jinja2 rendering) for ArgoCD and Flux stages.

For each deployment stage that references a ``sync``-capable integration via
``stage.backend.integration``, this builder:

1. Loads the platform artifact (``platform.json``) produced by ``PlatformBuilder``.
2. Looks up the integration in the configuration service.
3. Reads the Jinja2 template from ``work_path/.strata/templates/<properties.template>``.
4. Builds the template context from ``PlatformSpecModel`` (secrets excluded).
5. Applies namespace scoping when the stage declares ``stage.namespace``.
6. Renders the template with ``StrictUndefined`` — missing variables are errors.
7. Writes the rendered output to
   ``build_path/<deployment>/<stage.name>/<properties.output_file>``.

Security: ``spec.secrets`` is explicitly excluded from every template context.
Secrets are never surfaced to rendered output files.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jinja2 import Environment, StrictUndefined, UndefinedError

from strata.builders.base_builder import BaseBuilder
from strata.models.platform_artifact_model import PlatformArtifactModel, PlatformSpecModel
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.services.configuration_service import ConfigurationService

# Strict Jinja2 environment — sync output files must be complete; undefined
# variables are an error (controller will reject incomplete manifests).
_STRICT_JINJA2 = Environment(
    loader=None,  # type: ignore[arg-type]
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)

_SYNC_CAPABILITY = "sync"
_TEMPLATES_SUBDIR = Path(".strata") / "templates"


class SyncBuilder(BaseBuilder):
    """Render Jinja2 sync manifests for ArgoCD / Flux stages during ``strata build run``."""

    def __init__(
        self,
        verbose: bool = False,
        configuration_service: Optional["ConfigurationService"] = None,
    ) -> None:
        super().__init__(verbose=verbose)
        self.configuration_service = configuration_service

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        platform_model: Optional[PlatformArtifactModel] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Render Jinja2 templates for all sync stages in the deployment.

        Args:
            deployment_service: Fully-loaded deployment service.
            work_path: Workspace root (used to resolve ``.strata/templates/``).
            build_path: Root build output directory.
            dry_run: When True, render in memory and log planned outputs without
                writing files.
            platform_model: Pre-assembled platform model (from PlatformBuilder).
                When ``None`` the builder reads ``platform.json`` from disk.
            solution_controller: Optional controller for canonical path helpers.

        Returns:
            True on success or when no sync stages are present.
        """
        try:
            sync_stages = self._find_sync_stages(deployment_service)
            if not sync_stages:
                return True  # nothing to do

            spec = self._load_spec(deployment_service, build_path, platform_model, solution_controller)
            if spec is None:
                return False

            deployment_build_path = deployment_service.get_build_path(build_path)

            for stage in sync_stages:
                ok = self._render_stage(
                    stage=stage,
                    spec=spec,
                    work_path=work_path,
                    deployment_service=deployment_service,
                    build_path=build_path,
                    deployment_build_path=deployment_build_path,
                    dry_run=dry_run,
                    solution_controller=solution_controller,
                )
                if not ok:
                    return False

            return True

        except Exception as exc:
            msg = f"Sync builder failed: {exc}"
            self.logger.exception("Sync builder failed", error=str(exc))
            self._errors.append(msg)
            return False

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_sync_stages(self, deployment_service: DeploymentService) -> List[Any]:
        """Return stages that have a sync backend (``stage.backend.integration`` set)."""
        model = deployment_service.model
        if model is None or not model.spec.stages:
            return []
        return [s for s in model.spec.stages if s.backend is not None and s.backend.integration]

    def _load_spec(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        platform_model: Optional[PlatformArtifactModel],
        solution_controller: Optional["SolutionController"],
    ) -> Optional[PlatformSpecModel]:
        """Return ``PlatformSpecModel`` — from the provided model or by reading platform.json."""
        if platform_model is not None:
            return platform_model.spec

        # Fall back to reading platform.json from the build path
        deployment_build_path = deployment_service.get_build_path(build_path)
        json_path = (
            solution_controller.get_platform_path(deployment_service, build_path)
            if solution_controller is not None
            else deployment_build_path / "platform.json"
        )
        if not json_path.exists():
            self._errors.append(f"platform.json not found at {json_path}. Run 'strata build run' first.")
            return None

        svc = PlatformService(path=str(json_path), data=None)
        is_valid, errors = svc.validate()
        if not is_valid or svc.model is None:
            self._errors.extend(errors)
            return None

        return svc.model.spec

    def _render_stage(
        self,
        stage: Any,
        spec: PlatformSpecModel,
        work_path: Path,
        deployment_service: DeploymentService,
        build_path: Path,
        deployment_build_path: Path,
        dry_run: bool,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Render the Jinja2 template for a single sync stage."""
        stage_name = str(stage.name)
        integration_name = stage.backend.integration

        # Resolve integration from configuration service
        integration = self._find_integration(integration_name)
        if integration is None:
            self._errors.append(
                f"Stage '{stage_name}': integration '{integration_name}' not found in configuration spec.integrations"
            )
            return False

        # Validate sync capability
        if _SYNC_CAPABILITY not in (integration.capabilities or set()):
            self._errors.append(
                f"Stage '{stage_name}': integration '{integration_name}' does not have "
                f"the '{_SYNC_CAPABILITY}' capability"
            )
            return False

        # Extract required properties
        props = integration.properties or {}
        template_rel = props.get("template")
        output_rel = props.get("output_file")

        if not template_rel:
            self._errors.append(
                f"Stage '{stage_name}': integration '{integration_name}' is missing 'properties.template'"
            )
            return False
        if not output_rel:
            self._errors.append(
                f"Stage '{stage_name}': integration '{integration_name}' is missing 'properties.output_file'"
            )
            return False

        # Resolve template file
        template_path = work_path / _TEMPLATES_SUBDIR / template_rel
        if not template_path.exists():
            self._errors.append(f"Stage '{stage_name}': template file not found: {template_path}")
            return False

        template_content = template_path.read_text(encoding="utf-8")

        # Build Jinja2 context
        context = self._build_context(stage, spec, props)
        if context is None:
            return False

        # Render
        rendered = self._render_template(stage_name, template_content, context)
        if rendered is None:
            return False

        # Write output (or dry-run log)
        output_path = (
            solution_controller.get_sync_output_path(deployment_service, build_path, stage_name, output_rel)
            if solution_controller is not None
            else deployment_build_path / stage_name / output_rel
        )
        if dry_run:
            self._messages.append(f"[DRY-RUN] Would write sync manifest for stage '{stage_name}' to: {output_path}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            if self.verbose:
                self._messages.append(f"Rendered sync manifest for stage '{stage_name}' to: {output_path}")
            self.logger.debug(
                "Sync manifest rendered",
                stage=stage_name,
                output=str(output_path),
            )

        return True

    def _build_context(
        self,
        stage: Any,
        spec: PlatformSpecModel,
        integration_props: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Serialize spec to a Jinja2 context dict, applying namespace scoping.

        Secrets are always excluded.
        """
        # Serialize spec — exclude secrets (security: never expose to templates)
        context: Dict[str, Any] = spec.model_dump(
            exclude={"secrets"},
            exclude_none=True,
            mode="json",
        )

        # Namespace scoping: filter modules and inject singular `namespace`
        stage_namespace = getattr(stage, "namespace", None)
        if stage_namespace:
            namespaces = spec.namespaces or []
            scoped_ns = next((ns for ns in namespaces if str(ns.name) == stage_namespace), None)
            if scoped_ns is None:
                self._errors.append(
                    f"Stage '{stage.name}': namespace '{stage_namespace}' not found "
                    "in platform artifact spec.namespaces"
                )
                return None

            # Replace `modules` with only those declared in the scoped namespace
            ns_module_names = {str(m.module) for m in (scoped_ns.modules or [])}
            filtered_modules = [
                m.model_dump(exclude_none=True, mode="json")
                for m in (spec.modules or [])
                if str(m.name) in ns_module_names
            ]

            context["namespace"] = scoped_ns.model_dump(exclude_none=True, mode="json")
            context["modules"] = filtered_modules

        # Inject integration properties as top-level `integration` key
        context["integration"] = integration_props

        return context

    def _render_template(
        self,
        stage_name: str,
        content: str,
        context: Dict[str, Any],
    ) -> Optional[str]:
        """Render a Jinja2 template string with StrictUndefined.

        Returns the rendered string, or None on error (error is recorded).
        """
        try:
            template = _STRICT_JINJA2.from_string(content)
            return template.render(context)
        except UndefinedError as exc:
            self._errors.append(f"Stage '{stage_name}': template variable not found — {exc}")
            return None
        except Exception as exc:
            self._errors.append(f"Stage '{stage_name}': Jinja2 rendering failed — {exc}")
            return None

    def _find_integration(self, name: str) -> Any:
        """Look up an integration by name from the configuration service."""
        if self.configuration_service is None:
            return None
        model = self.configuration_service.model
        if model is None:
            return None
        for integ in model.spec.integrations or []:
            if integ.name == name:
                return integ
        return None
