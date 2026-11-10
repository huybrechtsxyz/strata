#!/usr/bin/env python3
"""The Jinja2 environment for handover document templates (docs/design/deployment-documentation.md, section 5),
and the check `strata validate` and `deploy report --template` run on a template.

In `utils` so `semantic_checks` can use the check without importing the report controllers. The renderer itself
is `controllers/deployment_document.py`.

Templates run in Jinja2's sandbox with `StrictUndefined`, so a typo fails loudly instead of rendering empty.
"""

from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.meta import find_undeclared_variables
from jinja2.sandbox import SandboxedEnvironment

#: The names a template may use: the report data and `MASK`.
DATA_NAMES = frozenset(
    ("deployment", "workspace", "environments", "steps", "secrets", "images", "audit", "generated_at", "MASK")
)
#: Plus Jinja2's own globals.
_KNOWN_NAMES = DATA_NAMES | {"range", "dict", "lipsum", "cycler", "joiner", "namespace", "loop"}


def md_cell(value: Any) -> str:
    """A value fit for one Markdown table cell: `|` escaped, line breaks as `<br>`, `None` as empty."""
    if value is None:
        return ""
    return str(value).replace("|", r"\|").replace("\r\n", "<br>").replace("\n", "<br>")


def report_environment() -> SandboxedEnvironment:
    """A sandboxed environment with `StrictUndefined` and the `md_cell` filter."""
    env = SandboxedEnvironment(undefined=StrictUndefined, keep_trailing_newline=True, trim_blocks=True)
    env.filters["md_cell"] = md_cell
    return env


def check_report_template(source: str) -> list[str]:
    """Problems with `source` as a report template: syntax errors and names the report data does not have.
    Empty when it is consistent (which does not prove it renders: nested keys are not checked)."""
    try:
        undeclared = find_undeclared_variables(report_environment().parse(source))
    except TemplateError as exc:
        return [f"template error: {exc}"]
    return [
        f"'{name}' is not in the report data; available: {', '.join(sorted(DATA_NAMES))}"
        for name in sorted(undeclared)
        if name not in _KNOWN_NAMES
    ]
