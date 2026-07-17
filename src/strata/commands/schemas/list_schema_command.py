"""Command to list supported platform schema kinds."""

from typing import Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.commands.schemas.schema_common import KIND_TO_MODEL
from strata.models.common_models import INTERNAL_KINDS, PlatformKind


class ListSchemaCommand(SchemaBaseCommand):
    """List all platform document kinds that have JSON schema models."""

    OPERATION = "schema_list"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)

    def _execute(self) -> bool:
        items = [
            {
                "kind": kind.value,
                "model": model.__name__,
                "internal": kind in INTERNAL_KINDS,
            }
            for kind, model in KIND_TO_MODEL.items()
        ]

        self._output_data = {
            "kinds": sorted(items, key=lambda x: x["kind"]),
        }
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        click.echo("")
        click.echo(f"  {'KIND':<24}  MODEL CLASS")
        click.echo(f"  {'-' * 24}  {'-' * 30}")
        for kind in PlatformKind:
            model_cls = KIND_TO_MODEL.get(kind)
            if model_cls:
                suffix = "  (internal)" if kind in INTERNAL_KINDS else ""
                click.echo(f"  {kind.value:<24}  {model_cls.__name__}{suffix}")
        click.echo("")
