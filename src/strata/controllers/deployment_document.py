#!/usr/bin/env python3
"""Render the handover document: `strata deploy report --format markdown` and `spec.documentation`
(docs/work/deployment-documentation.md, sections 4 and 5).

One renderer for both, over the same data as `deploy report --output json` (`DeploymentReport.to_data()`) plus
`audit` and `generated_at`. The built-in Markdown is itself a template (`templates/report/handover.md.j2`); a
user's `--template` or `spec.documentation.template` replaces it. No field in the data holds a secret value, so no
template can print one; `MASK` is what the document shows instead. The environment and the template check are in
`utils/report_template.py`.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import TemplateError

from strata.controllers.deployment_report_view import MASK, DeploymentReport
from strata.utils.report_template import check_report_template, report_environment

#: The built-in Markdown document.
BUILTIN_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "report" / "handover.md.j2"


class DocumentTemplateError(Exception):
    """A template does not parse, uses a name the report does not have, or fails to render."""


def render_document(
    report: DeploymentReport,
    *,
    template_source: str | None = None,
    audit: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render `report` with `template_source`, or the built-in Markdown when it is `None`. `audit` replaces the
    report's own (the last deploy recorded in the output store), for the run that is finishing right now.

    Raises:
        DocumentTemplateError: the template has a problem `check_report_template()` finds, or fails to render.
    """
    source = template_source if template_source is not None else BUILTIN_TEMPLATE.read_text(encoding="utf-8")
    problems = check_report_template(source)
    if problems:
        raise DocumentTemplateError("; ".join(problems))
    context = {
        **report.to_data(),
        "audit": audit if audit is not None else report.audit,
        "generated_at": (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC"),
        "MASK": MASK,
    }
    try:
        return report_environment().from_string(source).render(context)
    except TemplateError as exc:
        raise DocumentTemplateError(str(exc)) from exc
