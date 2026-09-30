#!/usr/bin/env python3
"""Scaffold-template mechanics (docs/design/solution-scaffolding.md).

Lives in `strata.utils` (below `strata.controllers` in the layered
architecture, ADR-0003): pure string/path logic, no filesystem I/O, no
Pydantic dependency — mirrors `path_safety.py`/`path_conventions.py`'s own
placement. `strata/controllers/scaffold_controller.py` owns the actual
directory walk/file-write orchestration; this module only answers three
narrow questions about one template file: what does it render to, where
does it land, and who owns it once it's there.

Deliberately a separate file from `strata/utils/templater.py` — that one is
tightly scoped to `output.template` (build-time-validate +
deploy-time-strict-render, per its own docstring). Scaffold templates use a
third, *lenient*-undefined Jinja2 environment (an unresolved token stays
literal rather than raising), since a scaffold template only ever gets a
small, fixed context — mixing that environment into `templater.py` would
blur which one applies where.
"""

from jinja2 import DebugUndefined, Environment

from strata.utils.layout import STRATA_DIR

#: Lenient — an undeclared variable is left visible in the output as literal
#: `{{ var }}` rather than raising. Appropriate here specifically because a
#: scaffold template's context is always small and fixed (`SOLUTION_NAME`,
#: `STRATA_VERSION`) — unlike `templater.py`'s `output.template`, which is
#: meant to fail loudly on an unresolvable reference.
_ENV = Environment(undefined=DebugUndefined, keep_trailing_newline=True, autoescape=False)


def render_scaffold(content: str, context: dict[str, str]) -> str:
    """Render a scaffold template string with `context`, leniently.

    Args:
        content: Template text using Jinja2 syntax (`{{ var }}`).
        context: Variable name -> replacement value.

    Returns:
        The rendered string. A variable not in `context` stays literal
        (`{{ var }}`) in the output rather than raising.
    """
    return _ENV.from_string(content).render(context)


def dest_relative_path(template_relative_path: str) -> str:
    """Rename every `dot.`-prefixed path segment to `.` (renders where a
    template source file actually lands in the target repo).

    `dot.github/pull_request_template.md` -> `.github/pull_request_template.md`;
    `dot.strata/README.md` -> `.strata/README.md`. A template source tree
    containing literal `.github/`/`.strata/` directories would be treated as
    *strata's own* config by every tool that scans for those names by
    convention (editors, CI systems, `git status`) during strata's own
    development — the `dot.` prefix sidesteps that ambiguity, for the cost
    of this one rename step at copy time. Always POSIX-separated in and out.
    """
    return "/".join("." + part[4:] if part.startswith("dot.") else part for part in template_relative_path.split("/"))


def is_package_owned(path: str) -> bool:
    """True when `path` (already renamed via `dest_relative_path()`) is
    under `.strata/` — always refreshed by `sln update`. False for anything
    else (`.github/` today) — written once by `sln init`, never touched by
    `update`, so a repo's own customization is never silently destroyed.

    A directory-level rule, not a per-prefix list (contrast v1's own fixed
    enumeration of `README.md`/`.gitignore`/`.devcontainer/`/`.github/`/two
    `.strata/` subpaths) — covers every future addition to either directory
    without needing a new entry added each time.
    """
    top_level = path.split("/", 1)[0]
    return top_level == STRATA_DIR
