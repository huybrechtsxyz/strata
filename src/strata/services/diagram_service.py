#!/usr/bin/env python3
"""Service for loading and validating diagram definitions (ADR-0034)."""

from typing import List, Optional, Tuple

from strata.models.diagram_model import DiagramModel
from strata.services.base_service import BaseService
from strata.utils.design_tokens import DESIGN_TOKENS
from strata.utils.diagram_expressions import ConditionError, parse_condition
from strata.utils.templater import TemplateProcessor


class DiagramService(BaseService["DiagramModel"]):
    """Service for handling diagram definitions."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the DiagramService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the DiagramModel class for validation."""
        return DiagramModel

    def _validate_self(self) -> Tuple[bool, List[str]]:
        """
        Phase 1.5: Self-consistency checks — no external dependencies required.

        Validates intra-document constraints that Pydantic model validators cannot check:
        - spec.template must be a usable Jinja2 template (valid syntax, known filters)
        - every variable the template references must be bound by spec.sources
        - spec.style.highlight rules must parse and name a real design token

        All of these would otherwise only surface at render time, long after the
        file was accepted.

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if not self.model:
            return True, []

        errors = self._validate_style()
        if errors:
            return False, errors

        template = self.model.spec.template
        if not template:
            # Nothing authored to check — the template is generated from
            # spec.layout / spec.style instead.
            return True, []

        diagram_name = self.model.meta.name

        # Syntax first: an unparseable template has no meaningful variable list,
        # so reporting both would bury the real error.
        syntax_error = TemplateProcessor.check_syntax(template)
        if syntax_error:
            return False, [f"Diagram '{diagram_name}': spec.template is not a usable template — {syntax_error}."]

        bound = {source.context_name for source in self.model.spec.sources or []}
        unbound = sorted(TemplateProcessor.find_variables(template) - bound)

        for name in unbound:
            available = sorted(bound) or ["<none — spec.sources is empty>"]
            errors.append(
                f"Diagram '{diagram_name}': spec.template references '{name}', which is not bound "
                f"by spec.sources. Add a source with 'as: {name}', or reference one of the bound "
                f"names: {available}."
            )

        return not errors, errors

    def _validate_style(self) -> List[str]:
        """Check highlight rules parse and name a real design token.

        Authored token names are checked strictly, unlike the data-derived names
        ``style.color_by`` produces: a value that turns up in the data can
        reasonably be one nobody anticipated, but a name typed into the YAML is
        a typo if it does not exist.
        """
        assert self.model is not None
        style = self.model.spec.style
        if not style or not style.highlight:
            return []

        diagram_name = self.model.meta.name
        errors: List[str] = []
        for rule in style.highlight:
            try:
                parse_condition(rule.condition)
            except ConditionError as exc:
                errors.append(f"Diagram '{diagram_name}': {exc}")
            if rule.token not in DESIGN_TOKENS:
                errors.append(
                    f"Diagram '{diagram_name}': highlight token '{rule.token}' is not a design "
                    f"token. Available: {sorted(DESIGN_TOKENS)}."
                )
        return errors

    def _validate_dynamic(
        self,
        configuration_model=None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Diagrams have no cross-file references — self-contained."""
        return True, []
