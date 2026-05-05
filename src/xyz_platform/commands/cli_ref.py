"""Click CLI wiring for the top-level ref command group.

Provides four subgroups — envfile, configfile, datafile, secretfile — each
with add / remove / list / show commands.  All commands accept an optional
``--profile`` option; when omitted the active profile is used.
"""

from typing import Optional

import click

from xyz_platform.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_profile,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.ref.add_profile_path_command import AddProfilePathCommand
from xyz_platform.commands.ref.list_profile_path_command import ListProfilePathCommand
from xyz_platform.commands.ref.remove_profile_path_command import RemoveProfilePathCommand
from xyz_platform.commands.ref.show_ref_command import ShowRefCommand

# ---------------------------------------------------------------------------
# Top-level ref group
# ---------------------------------------------------------------------------


@click.group(name="ref", help="Manage file references (envfile, configfile, datafile, secretfile) within profiles.")
def ref_group():
    """Ref command group."""
    pass


# ===========================================================================
# Helper: build add / remove / list / show for one file type
# ===========================================================================


def _build_file_type_group(type_name: str, description: str) -> click.Group:
    """Return a fully wired Click group for *type_name* (e.g. 'envfile')."""

    @click.group(name=type_name, help=description)
    def _group():
        pass

    # -------------------------------------------------------------------
    # add
    # -------------------------------------------------------------------

    @_group.command(name="add", help=f"Register a {type_name} path entry in a profile.")
    @click.argument("name")
    @click.argument("path")
    @click_profile
    @click_work_path
    @click_output_format
    @click_output_verbose
    @click_output_quiet
    def _add(
        name: str,
        path: str,
        profile: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        # When profile is None the command will fail in _before_execute with a
        # clear message; we propagate None so AddProfilePathCommand can report it.
        profile_name = profile or ""
        command = AddProfilePathCommand(
            profile=profile_name,
            type=type_name,
            name=name,
            path=path,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        success = command.execute()
        handle_command_exit(command, success)

    # -------------------------------------------------------------------
    # remove
    # -------------------------------------------------------------------

    @_group.command(name="remove", help=f"Remove a {type_name} path entry from a profile.")
    @click.argument("name")
    @click_profile
    @click_work_path
    @click_output_format
    @click_output_verbose
    @click_output_quiet
    def _remove(
        name: str,
        profile: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        profile_name = profile or ""
        command = RemoveProfilePathCommand(
            profile=profile_name,
            type=type_name,
            name=name,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        success = command.execute()
        handle_command_exit(command, success)

    # -------------------------------------------------------------------
    # list
    # -------------------------------------------------------------------

    @_group.command(name="list", help=f"List all {type_name} path entries for a profile.")
    @click_profile
    @click_work_path
    @click_output_format
    @click_output_verbose
    @click_output_quiet
    def _list(
        profile: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        # ListProfilePathCommand lists all types; we filter by type_name in _after_execute.
        # For now, use it directly — the profile argument is required by the command class.
        # If profile is None we pass an empty string and the command resolves the active one.
        profile_name = profile or ""
        command = _ListSingleTypeCommand(
            profile=profile_name,
            path_type=type_name,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        success = command.execute()
        handle_command_exit(command, success)

    # -------------------------------------------------------------------
    # show
    # -------------------------------------------------------------------

    @_group.command(name="show", help=f"Display the file content of a {type_name} path entry.")
    @click.argument("name")
    @click_profile
    @click_work_path
    @click_output_format
    @click_output_verbose
    @click_output_quiet
    def _show(
        name: str,
        profile: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        command = ShowRefCommand(
            path_type=type_name,
            name=name,
            profile=profile,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        success = command.execute()
        handle_command_exit(command, success)

    return _group


# ---------------------------------------------------------------------------
# Thin command subclass for list — filters to a single type
# ---------------------------------------------------------------------------


class _ListSingleTypeCommand(ListProfilePathCommand):
    """Variant of ListProfilePathCommand that shows only one path type.

    When ``profile`` is empty the active profile is resolved in _before_execute.
    """

    def __init__(self, profile: str, path_type: str, **kwargs) -> None:
        super().__init__(profile=profile, **kwargs)
        self._filter_type = path_type

    def _before_execute(self) -> bool:
        # Resolve active profile when profile arg was not supplied
        if not self._profile_name:
            active, errors = self._solution_controller.get_active_profile()
            if errors:
                self._errors.extend(errors)
                return False
            if active is None:
                self._errors.append("No active profile found. Use --profile to specify one.")
                return False
            self._profile_name = str(active.name)

        return super()._before_execute()

    def _run_execution(self) -> bool:
        ok = super()._run_execution()
        if not ok:
            return False
        # Narrow to the single requested type
        filtered = {k: v for k, v in self._paths.items() if k == self._filter_type}
        self._paths = filtered
        self._output_data = {"profile": self._profile_name, "paths": self._paths}
        return True


# ---------------------------------------------------------------------------
# Register the four file-type subgroups
# ---------------------------------------------------------------------------

ref_group.add_command(_build_file_type_group("envfile", "Manage env-file references within a profile."))
ref_group.add_command(_build_file_type_group("configfile", "Manage config-file references within a profile."))
ref_group.add_command(_build_file_type_group("datafile", "Manage data-file references within a profile."))
ref_group.add_command(_build_file_type_group("secretfile", "Manage secret-file references within a profile."))
