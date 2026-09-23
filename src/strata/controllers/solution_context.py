#!/usr/bin/env python3
"""Opening a solution — the precondition every entry point shares.

Locating the root, loading it, and deciding what counts as "good enough to
proceed" must behave identically for every caller. Left to each one, those
decisions drift: different messages for the same missing `strata.yaml`,
different handling of a half-loaded index, and eventually commands that
operate on documents that never parsed.

Lives in the controller layer, not the command layer, because none of it is
CLI-specific — a server or MCP front-end needs exactly the same entry point.
What differs per front-end is only how a failure is *reported*, which is why
the errors raised here carry no exit code.

Callers differ in one way:

- `validate` renders the findings and decides from severity.
- Everything that acts on configuration calls `require_valid()` first,
  because building or deploying from a partially-loaded index is never right.
"""

from dataclasses import dataclass
from pathlib import Path

from strata.controllers.references import validate_references
from strata.controllers.solution_controller import SolutionController, find_solution_root
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError, ValidationError
from strata.utils.layout import MANIFEST_FILENAME


@dataclass(frozen=True)
class SolutionContext:
    """A loaded solution and everything found while loading it."""

    controller: SolutionController
    diagnostics: Diagnostics

    @property
    def root(self) -> Path:
        """The solution root — the directory holding `strata.yaml`."""
        return self.controller.root

    @property
    def ok(self) -> bool:
        """True when no document recorded an error."""
        return self.diagnostics.ok

    def require_valid(self) -> "SolutionContext":
        """Return self, or raise if the solution is not fully valid.

        Runs `resolve()` first, so a caller that acts on configuration cannot
        skip cross-document checks: deploying a workspace that names a
        provider which does not exist fails later and less clearly.

        A document that fails schema validation never enters the index, so
        everything referencing it then reports as missing — one real error
        becomes a screenful of derived ones. `resolve()` short-circuits for
        that reason, and the first error stays the actionable one.

        Raises:
            ValidationError: Carrying the findings, so the caller can render
                them before deciding what to do.
        """
        self.resolve()
        if not self.ok:
            raise ValidationError(self.diagnostics)
        return self


    def resolve(self) -> Diagnostics:
        """Run cross-document checks and merge the findings in.

        Only meaningful once every document loaded: a document that failed
        schema validation never entered the index, so reference checks would
        report "unknown workspace 'main'" when the truth is that `main` did
        not parse. One real error would become a screenful of derived ones, so
        this returns immediately when loading already failed.

        Returns:
            The findings from this pass. They are also merged into
            `self.diagnostics`, so `ok` accounts for them.
        """
        found = Diagnostics()
        if not self.ok:
            return found

        found.extend(validate_references(self.controller.index, self.controller.solution))
        self.diagnostics.extend(found)
        return found


def open_solution(path: Path | None = None) -> SolutionContext:
    """Find the solution containing `path`, load it, and return the result.

    Args:
        path: Where to start looking. Defaults to the current directory.
            The search walks upwards, so any subdirectory of a solution works.

    Returns:
        The loaded solution and its findings. Findings are *not* fatal here —
        `validate` needs to render them, so the decision to stop belongs to
        the caller via `require_valid()`.

    Raises:
        UsageError: If `path` is not inside a solution at all.
    """
    start = (path or Path.cwd()).resolve()

    if not start.exists():
        raise UsageError(f"Path does not exist: {start}")

    root = find_solution_root(start)
    if root is None:
        raise UsageError(
            f"Not inside a strata solution: no {MANIFEST_FILENAME} found in '{start}' "
            f"or any parent directory."
        )

    controller = SolutionController(root)
    return SolutionContext(controller=controller, diagnostics=controller.load())
