#!/usr/bin/env python3
"""Solution scaffolding — `strata sln init`/`strata sln update`
(docs/design/solution-scaffolding.md).

A clean split from `solution_controller.py`, which is purely document
discovery/loading — this controller's only job is filesystem scaffolding
(creating `strata.yaml`, copying the package's own `templates/solution/`
tree into a target repo). Keeping the two separate avoids growing
`SolutionController` into a second, unrelated responsibility.

`init()` and `update()` share one `_scaffold()` step — the only difference
between them is whether `strata.yaml` gets created first (`init()` does,
only if missing; `update()` requires it to already exist). Ownership is a
directory-level rule, not a per-file list: `.strata/` is always refreshed
(package-owned — nothing under it is meant to be hand-edited), `.github/`
is written once and never touched again (user-owned — a PR/issue template
is exactly the kind of file a repo commonly tailors per-org).
"""

import json
from pathlib import Path
from typing import Any

import yaml

from strata.controllers.solution_controller import SERVICE_BY_KIND
from strata.models.common_models import PlatformVersion
from strata.utils.errors import UsageError
from strata.utils.layout import (
    MANIFEST_FILENAME,
    SCHEMAS_DIRNAME,
    STRATA_DIR,
    UMBRELLA_SCHEMA_FILENAME,
    manifest_path,
    schemas_dir,
)
from strata.utils.scaffold_templates import dest_relative_path, is_package_owned, render_scaffold
from strata.utils.version import get_version

#: Suffixes copied verbatim, never rendered — `.j2` files are themselves raw
#: Jinja2 templates meant for a *later*, end-user-driven render pass
#: (unrelated to this one); `.md` files are documentation whose own example
#: code (which may contain literal `{{ }}`) must not be touched.
_NEVER_RENDER_SUFFIXES = (".j2", ".md")

#: `src/strata/templates/solution/` — this file lives at
#: `src/strata/controllers/scaffold_controller.py`, so `.parent.parent`
#: reaches `src/strata/`.
_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates" / "solution"


class ScaffoldController:
    """Scaffolds/refreshes the files strata itself owns in a solution repo."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.messages: list[str] = []

    def init(self, name: str) -> None:
        """Create `strata.yaml` if missing, then scaffold/refresh the template tree.

        Never overwrites an existing `strata.yaml` — even to change
        `meta.name` — so re-running `init` on an already-initialised
        solution is always safe (docs/design/solution-scaffolding.md's
        Open Question #3).
        """
        self._ensure_manifest(name)
        self._scaffold()

    def update(self) -> None:
        """Refresh package-owned scaffold files (`.strata/`) in an existing solution.

        Raises:
            UsageError: No `strata.yaml` at `self.root` — nothing to update.
        """
        if not manifest_path(self.root).is_file():
            raise UsageError(
                f"No {MANIFEST_FILENAME} found at '{self.root}' — not a strata solution. "
                "Run 'strata sln init <name>' first."
            )
        self._scaffold()

    def _ensure_manifest(self, name: str) -> None:
        path = manifest_path(self.root)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"apiVersion: {PlatformVersion.v2.value}\nkind: solution\nmeta:\n  name: {name}\nspec: {{}}\n",
            encoding="utf-8",
        )
        self.messages.append(f"Created: {path.relative_to(self.root)}")

    def _scaffold(self) -> None:
        context = {"SOLUTION_NAME": self._solution_name(), "STRATA_VERSION": get_version()}
        for src in sorted(_TEMPLATE_ROOT.rglob("*")):
            if not src.is_file():
                continue
            relative = dest_relative_path(src.relative_to(_TEMPLATE_ROOT).as_posix())
            dest = self.root / relative
            owned = is_package_owned(relative)
            if not owned and dest.exists():
                continue  # user-owned and already present — never touched

            content = src.read_text(encoding="utf-8")
            if src.suffix not in _NEVER_RENDER_SUFFIXES:
                content = render_scaffold(content, context)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            self.messages.append(f"{'Updated' if owned else 'Created'}: {relative}")
        self._export_schemas()

    def _export_schemas(self) -> None:
        """Write one JSON Schema per registered kind, plus a `kind:`-dispatching
        umbrella schema, to `.strata/schemas/` — derived artifacts, always
        regenerated (same "package-owned, always refreshed" rule as the rest
        of `.strata/`), never hand-edited.

        Reuses `SERVICE_BY_KIND` (`solution_controller.py`) as the one
        source of truth for "every registered kind" — deliberately not a
        second, separately-maintained kind list (v1 had two independent
        lists for this exact feature that silently drifted apart).

        Each per-kind file is `model_cls.model_json_schema()` unmodified —
        no field stripping, no added `$schema`/`$id`. The umbrella
        (`strata.json`) is the only synthesized schema: an `if`/`then` chain
        keyed on the document's own `kind:` value, `$ref`-ing the matching
        per-kind file, so one `yaml.schemas` mapping covers every kind at
        once instead of one entry per kind/directory glob.
        """
        schemas_directory = schemas_dir(self.root)
        schemas_directory.mkdir(parents=True, exist_ok=True)
        branches: list[dict[str, Any]] = []
        for kind, service_cls in SERVICE_BY_KIND.items():
            # Every service exposes its own model class this way; a throwaway
            # instance (never validated against any real data) is the
            # cheapest way to reach it without a second kind->model registry.
            model_cls = service_cls(data={})._get_model_class()
            filename = f"{kind.value}.json"
            (schemas_directory / filename).write_text(
                json.dumps(model_cls.model_json_schema(), indent=2) + "\n", encoding="utf-8"
            )
            branches.append(
                {
                    "if": {"properties": {"kind": {"const": kind.value}}, "required": ["kind"]},
                    "then": {"$ref": filename},
                }
            )
        umbrella = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Strata Platform Configuration",
            "description": "Dispatches to the matching per-kind schema in this same directory, based on "
            "the document's own 'kind:' field.",
            "type": "object",
            "properties": {"apiVersion": {"type": "string"}, "kind": {"type": "string"}},
            "allOf": branches,
        }
        (schemas_directory / UMBRELLA_SCHEMA_FILENAME).write_text(
            json.dumps(umbrella, indent=2) + "\n", encoding="utf-8"
        )
        self.messages.append(f"Updated: {STRATA_DIR}/{SCHEMAS_DIRNAME}/ ({len(branches)} kinds + umbrella)")

    def _solution_name(self) -> str:
        """The manifest's `meta.name`, for template substitution — read back
        rather than threaded through as a parameter, so `update()` (which has
        no `name` of its own) gets an accurate value too."""
        path = manifest_path(self.root)
        if not path.is_file():
            return ""
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str(data.get("meta", {}).get("name", ""))
