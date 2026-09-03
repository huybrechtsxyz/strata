"""Shared, kind-discriminated expression model (ADR-0073).

Replaces shape-sniffed string dispatch (e.g. ``path_convention.py``'s
``is_spec_rule()`` regex) with an explicit ``kind:`` field. Each kind's compiled
form is built once, at model-validation time, and cached — since the owning
``ConfigurationModel`` is loaded once per CLI invocation but evaluated once per
file during a workspace scan, recompiling per-file would be wasted work.

Only ``path`` and ``yaml`` kinds are wired into a real call site today
(``PathConventionModel.rules``, see ``path_convention.py``). ``regex``/``jinja``
are defined for completeness — per this ADR's own scope rule, a `kind:`
discriminator is only warranted where the *same schema position* can validly
hold more than one kind of expression, which is not yet true anywhere else in
the codebase (``gate_controller.py``/``diagram_expressions.py`` each have their
own single, unambiguous grammar and don't need one).
"""

import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import PrivateAttr, model_validator

from strata.models.common_models import PlatformBaseModel


class ExpressionKind(str, Enum):
    """What ``expression`` means, and which engine evaluates it."""

    PATH = "path"
    """``{segment}`` substitution + file-existence check."""

    YAML = "yaml"
    """JMESPath query against structured config data."""

    REGEX = "regex"
    """Fixed-shape pattern match/extract, no evaluation semantics. Not yet wired
    to a call site — defined for completeness (see module docstring)."""

    JINJA = "jinja"
    """Boolean/comparison expression evaluation. Not yet wired to a call site —
    defined for completeness (see module docstring)."""


class ExpressionModel(PlatformBaseModel):
    """A single evaluatable expression, tagged with an explicit kind.

    ``expression``'s meaning is fully determined by ``kind`` — never by
    sniffing the string's own shape.
    """

    kind: ExpressionKind
    expression: str

    # Compiled form — built once in _compile() below, reused for every
    # evaluation. Excluded from serialization (not JSON-safe; re.Pattern /
    # jmespath's parsed-expression object / a Jinja callable aren't dumpable).
    _compiled: Any = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _compile(self) -> "ExpressionModel":
        """Compile once at load time; catches a broken expression immediately
        (at YAML-parse time) instead of at first-use deep in a workspace scan."""
        if self.kind == ExpressionKind.YAML:
            import jmespath
            from jmespath.exceptions import ParseError

            try:
                self._compiled = jmespath.compile(self.expression)
            except ParseError as e:
                raise ValueError(f"invalid JMESPath expression {self.expression!r}: {e}") from e

        elif self.kind == ExpressionKind.REGEX:
            try:
                self._compiled = re.compile(self.expression)
            except re.error as e:
                raise ValueError(f"invalid regex {self.expression!r}: {e}") from e

        elif self.kind == ExpressionKind.JINJA:
            from jinja2 import Environment, TemplateSyntaxError

            try:
                self._compiled = Environment().compile_expression(self.expression)
            except TemplateSyntaxError as e:
                raise ValueError(f"invalid jinja expression {self.expression!r}: {e}") from e

        # PATH kind needs no compilation — {segment} substitution + the
        # existence check happen per-call in check_path(), which delegates to
        # path_convention.py's evaluate_file_rule() (reused, not duplicated).
        return self

    # --- Kind-specific evaluation (deliberately not one polymorphic method —
    # the four kinds take genuinely different inputs: dict / string / dict /
    # (captures, work_path)) ---

    def query(self, data: dict) -> Any:
        """Run a ``kind=yaml`` expression against *data* (e.g. a ``model_dump()``).

        Raises:
            ValueError: If ``kind`` is not ``yaml``.
        """
        if self.kind != ExpressionKind.YAML:
            raise ValueError(f"query() requires kind=yaml, got kind={self.kind.value}")
        return self._compiled.search(data)

    def matches(self, value: str) -> bool:
        """Return True if *value* matches a ``kind=regex`` expression.

        Raises:
            ValueError: If ``kind`` is not ``regex``.
        """
        if self.kind != ExpressionKind.REGEX:
            raise ValueError(f"matches() requires kind=regex, got kind={self.kind.value}")
        return bool(self._compiled.match(value))

    def evaluate(self, context: dict) -> Any:
        """Evaluate a ``kind=jinja`` expression against *context*.

        Raises:
            ValueError: If ``kind`` is not ``jinja``.
        """
        if self.kind != ExpressionKind.JINJA:
            raise ValueError(f"evaluate() requires kind=jinja, got kind={self.kind.value}")
        return self._compiled(**context)

    def check_path(self, captures: Dict[str, str], work_path: Path) -> Optional[str]:
        """Check a ``kind=path`` file-existence expression.

        Substitutes ``{segment}`` captures into ``expression`` and checks the
        resulting path exists under *work_path*. Reuses (does not duplicate)
        ``strata.utils.path_convention.evaluate_file_rule()`` — the shared
        implementation of this substitution + existence check, imported
        lazily here to avoid a circular import once ``path_convention.py``'s
        ``rules:`` dispatch is wired to construct/consume ``ExpressionModel``.

        Returns:
            ``None`` on success (file exists, or a "WARN:"-prefixed
            self-reference case — see ``evaluate_file_rule()``), or a
            violation message string if the file does not exist.

        Raises:
            ValueError: If ``kind`` is not ``path``.
        """
        if self.kind != ExpressionKind.PATH:
            raise ValueError(f"check_path() requires kind=path, got kind={self.kind.value}")
        from strata.utils.path_convention import evaluate_file_rule

        return evaluate_file_rule(self.expression, captures, work_path)
