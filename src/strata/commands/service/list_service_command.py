"""List all services (namespace × module) in a deployment."""

import click

from strata.commands.service.base_service_command import BaseServiceCommand


class ListServiceCommand(BaseServiceCommand):
    """List all service targets in the deployment definition."""

    OPERATION = "service_list"

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self._finalize(success=False)
                return False

            targets = self._resolve_all_targets()

            if not targets:
                if self._is_console_output():
                    click.echo("No services found in deployment definition.")
                self._finalize(success=True)
                return True

            if self._is_console_output():
                click.echo(f"\n{'Namespace':<20} {'Module':<20} {'Type':<10} {'Build Path'}")
                click.echo(f"{'─' * 20} {'─' * 20} {'─' * 10} {'─' * 40}")
                for t in targets:
                    click.echo(f"{t.namespace:<20} {t.module or '—':<20} {t.deployer_type.value:<10} {t.build_path}")
                click.echo(f"\n{len(targets)} service(s) found.")

            self._output_data["services"] = [
                {
                    "namespace": t.namespace,
                    "module": t.module,
                    "type": t.deployer_type.value,
                    "build_path": str(t.build_path),
                }
                for t in targets
            ]

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to list services: {exc}")
            self.logger.exception("service_list failed")
            self._finalize(success=False)
            return False
