"""Command to emit the JSON schema for one platform kind."""

from typing import Any, Dict, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.commands.schemas.schema_common import KIND_TO_MODEL
from strata.models.common_models import PlatformKind


class GetSchemaCommand(SchemaBaseCommand):
    """Emit the JSON schema for one platform document kind."""

    OPERATION = "schema_get"

    def __init__(
        self,
        kind: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._kind_raw = kind

    def _execute(self) -> bool:
        try:
            platform_kind = PlatformKind(self._kind_raw.lower())
        except ValueError:
            valid = ", ".join(sorted(kind.value for kind in KIND_TO_MODEL))
            self._errors.append(f"Unknown kind '{self._kind_raw}'. Valid kinds: {valid}")
            return False

        model_cls = KIND_TO_MODEL.get(platform_kind)
        if model_cls is None:
            self._errors.append(f"No schema available for kind '{self._kind_raw}'.")
            return False

        schema: Dict[str, Any] = model_cls.model_json_schema()  # type: ignore[attr-defined]
        self._output_data = {
            "kind": platform_kind.value,
            "model": schema.get("title", model_cls.__name__),
            "required": schema.get("required", []),
            "properties": list(schema.get("properties", {}).keys()),
            "schema": schema,
        }
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            self._render_console()
        return super()._after_execute()

    def _render_console(self) -> None:
        required = self._output_data.get("required", [])
        properties = self._output_data.get("properties", [])
        click.echo("")
        click.echo(f"  Kind:       {self._output_data.get('kind')}")
        click.echo(f"  Model:      {self._output_data.get('model')}")
        click.echo(f"  Required:   {', '.join(required) if required else '(none)'}")
        click.echo(f"  Properties: {', '.join(properties)}")
        click.echo("")
        click.echo("  Run with --output json to get the full JSON Schema envelope.")
        click.echo("")
