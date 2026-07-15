"""Shared base for all promote command classes."""

from __future__ import annotations

from strata.commands.base_command import BaseCommand


class BasePromoteCommand(BaseCommand):
    """Base class for ``strata promote`` subcommands.

    Promote commands operate without a mandatory strata workspace init —
    they do their own workspace discovery. Output envelope is suppressed;
    each command renders its own output in ``_render()``.
    """

    SHOW_CHROME = False

    def _initialize(self, show_header: bool = True) -> bool:
        # Promote commands work without an initialized workspace.
        return self._initialize_session(show_header=show_header)

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """Suppress BaseCommand lifecycle envelope — commands render their own output."""
        return success
