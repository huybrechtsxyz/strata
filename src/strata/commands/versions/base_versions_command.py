"""Shared base for all versions command classes."""

from __future__ import annotations

from strata.commands.base_command import BaseCommand


class BaseVersionsCommand(BaseCommand):
    """Base class for ``strata versions`` subcommands.

    Versions commands operate on YAML files directly; they do not require an
    initialized strata workspace (no ``solution.json``) and produce no chrome.

    ``_finalize()`` is suppressed because each command renders its own output
    inside ``_run()`` / ``_render()``.  The ``BaseCommand`` structured-output
    envelope (JSON/text lifecycle wrapper) must not be emitted twice.
    """

    SHOW_CHROME = False

    def _initialize(self, show_header: bool = True) -> bool:
        # Versions commands work without an initialized workspace.
        return self._initialize_session(show_header=show_header)

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """Suppress BaseCommand lifecycle envelope — commands render their own output."""
        return success
