#!/usr/bin/env python3
"""Controller for locating and rendering ``kind: diagram`` definitions (ADR-0034).

Built-in diagrams are shipped ``kind: diagram`` YAML files rather than hardcoded
renderers, so ``show -f topology`` and ``show -f ./mine.yaml`` take the identical
code path — a built-in can be copied into ``.strata/diagrams/`` and edited like
any other definition.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.controllers.base_controller import BaseController
from strata.controllers.diagram_source_controller import DiagramSourceController
from strata.controllers.diagram_template_builder import TemplateBuildError, build_template
from strata.models.diagram_model import DiagramModel
from strata.services.configuration_service import ConfigurationService
from strata.services.diagram_service import DiagramService
from strata.utils.config import get_diagrams_dir
from strata.utils.system import get_pkg_diagrams_path
from strata.utils.templater import TemplateProcessor

BUILT_IN = "built-in"
WORKSPACE = "workspace"


class DiagramController(BaseController):
    """Locate diagram definitions and render them to Mermaid source."""

    def __init__(self, work_path: Path, entry: Optional[str] = None, no_validate: bool = False) -> None:
        super().__init__()
        self._work_path = work_path
        self._entry = entry
        self._no_validate = no_validate

    # ─── Discovery ────────────────────────────────────────────────────────────

    def list_definitions(self) -> List[Dict[str, Any]]:
        """List every available definition, built-ins first.

        A workspace definition shadows a built-in of the same name, matching the
        resolution order in :meth:`resolve_definition`.
        """
        by_name: Dict[str, Dict[str, Any]] = {}
        for source, directory in ((BUILT_IN, get_pkg_diagrams_path()), (WORKSPACE, get_diagrams_dir(self._work_path))):
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.yaml")):
                entry = self._describe(path, source)
                if entry:
                    by_name[entry["name"]] = entry
        return sorted(by_name.values(), key=lambda e: e["name"])

    def resolve_definition(self, name_or_path: str) -> Optional[Path]:
        """Resolve a definition name or path to a file.

        Order: explicit path, then ``<name>.yaml`` in the diagram directories,
        then a document whose ``meta.name`` matches. The last step matters
        because ``strata diagram list`` reports ``meta.name`` — without it a user
        could see a diagram listed and be unable to render it by that name.

        Workspace definitions are searched before built-ins so a user can
        override a built-in without renaming it.
        """
        candidate = Path(name_or_path)
        for path in (candidate, self._work_path / candidate):
            if path.is_file():
                return path

        stem = candidate.stem if candidate.suffix == ".yaml" else name_or_path
        directories = (get_diagrams_dir(self._work_path), get_pkg_diagrams_path())
        for directory in directories:
            path = directory / f"{stem}.yaml"
            if path.is_file():
                return path

        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.yaml")):
                entry = self._describe(path, WORKSPACE)
                if entry and entry["name"] == name_or_path:
                    return path

        self._add_error(
            f"Diagram '{name_or_path}' not found. Looked for a file at that path, "
            f"then '{stem}.yaml' and a document named '{name_or_path}' in .strata/diagrams/ "
            f"and in the built-in definitions. Run 'strata diagram list' to see what is available."
        )
        return None

    # ─── Rendering ────────────────────────────────────────────────────────────

    def load(self, path: Path) -> Optional[DiagramModel]:
        """Load and validate a diagram definition."""
        service = DiagramService(str(path))
        is_valid, errors = service.validate()
        if not is_valid:
            for error in errors:
                self._add_error(error)
            return None
        return service.get_model()

    def get_template(self, model: DiagramModel) -> Optional[str]:
        """Return the Jinja template that will be rendered.

        An authored ``spec.template`` always wins; ``spec.layout``/``spec.style``
        generate one instead. Both then take the identical render path, which is
        why ``--print-template`` can hand a user the generated source to edit.
        """
        if model.spec.template:
            return model.spec.template
        if model.spec.layout is None:
            self._add_error(f"Diagram '{model.meta.name}' has neither 'spec.template' nor 'spec.layout'.")
            return None
        try:
            return build_template(model.spec.layout, model.spec.style, model.spec.sources)
        except TemplateBuildError as exc:
            self._add_error(f"Diagram '{model.meta.name}': {exc}")
            return None

    def render(
        self, model: DiagramModel, configuration_service: Optional[ConfigurationService] = None
    ) -> Optional[str]:
        """Render *model* to Mermaid source.

        Args:
            model: The loaded diagram definition.
            configuration_service: Active profile's configuration, if the caller
                already loaded one. Only the ``policies`` source needs it — every
                other source loads from a bare path with no profile dependency.
        """
        template = self.get_template(model)
        if template is None:
            return None

        source_controller = DiagramSourceController(
            self._work_path,
            entry=self._entry,
            no_validate=self._no_validate,
            configuration_service=configuration_service,
        )
        context = source_controller.resolve(model.spec.sources)
        if source_controller.has_errors():
            for error in source_controller.get_errors():
                self._add_error(error)
            return None

        return TemplateProcessor.render(template, context)

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _describe(self, path: Path, source: str) -> Optional[Dict[str, Any]]:
        """Summarise a definition for ``list``, skipping anything unreadable."""
        service = DiagramService(str(path))
        is_valid, _errors = service.validate()
        if not is_valid:
            self.logger.debug("Skipping invalid diagram definition", path=str(path))
            return None
        model = service.get_model()
        annotations = model.meta.annotations or {}
        return {
            "name": model.meta.name,
            "description": annotations.get("description", ""),
            "source": source,
            "path": str(path),
            "sources": [s.type.value for s in model.spec.sources or []],
        }
