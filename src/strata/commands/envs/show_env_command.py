"""Command to display the full resolved environment configuration for a deployment."""

from typing import Any, Dict, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ValueController


def _mask(value: Any) -> str:
    """Mask a secret value for safe display."""
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 4)


class ShowEnvCommand(BaseDeployCommand):
    """Show the full resolved environment configuration for a deployment.

    Displays:
      - Environment metadata (name, labels, annotations)
      - Properties and custom settings
      - Resolved variables (full values)
      - Resolved secrets (masked)
      - Resolved feature flags
      - Overrides summary (resources, modules, providers)
      - Stage list with provisioner types
    """

    OPERATION = "env_show"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._stage = stage

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            ok = self._run()
            self._after_execute()
            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute env_show: {exc}")
            self.logger.exception("env_show failed")
            self._finalize(success=False)
            return False

    def _run(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deployment_model = self._deployment_service.model
        env_service = self._deployment_service.get_environment_service()

        # Build the output data structure
        data: Dict[str, Any] = {
            "file": str(self._file_path),
            "deployment": str(deployment_model.meta.name),
        }

        # --- Stages ---
        stages = deployment_model.spec.stages or []
        stage_rows = []
        for s in stages:
            row: Dict[str, Any] = {
                "name": str(s.name),
                "provisioner": s.provisioner or "terraform",
                "scope": s.scope or None,
            }
            if s.depends_on:
                row["depends_on"] = [str(d) for d in s.depends_on]
            stage_rows.append(row)
        data["stages"] = stage_rows

        # --- Environment ---
        if env_service is None:
            data["environment"] = None
            if self._is_console_output():
                click.echo("\n⚠️   No environment attached to this deployment.\n")
            self._output_data = data
            return True

        env_model = env_service.model
        env_data: Dict[str, Any] = {}

        # Meta
        if env_model and env_model.meta:
            env_data["name"] = str(env_model.meta.name)
            if env_model.meta.labels:
                env_data["labels"] = dict(env_model.meta.labels)
            if env_model.meta.annotations:
                env_data["annotations"] = dict(env_model.meta.annotations)

        # Properties & custom
        if env_model and env_model.spec:
            if env_model.spec.properties:
                env_data["properties"] = dict(env_model.spec.properties)
            if env_model.spec.custom:
                env_data["custom"] = dict(env_model.spec.custom)

        # Resolve values
        controller = ValueController()
        _, resolved, _ = controller.resolve_values(self._deployment_service, strict=False)

        # Variables
        declared_vars = env_service.get_variables()
        var_rows = []
        for item in declared_vars:
            val = resolved.variables.get(item.key)
            var_rows.append(
                {
                    "key": item.key,
                    "value": str(val) if val is not None else None,
                    "store": item.store.value,
                    "resolved": item.key in resolved.variables,
                }
            )
        env_data["variables"] = var_rows

        # Secrets (masked)
        declared_secrets = env_service.get_secrets()
        secret_rows = []
        for item in declared_secrets:
            val = resolved.secrets.get(item.key)
            secret_rows.append(
                {
                    "key": item.key,
                    "value": _mask(val) if val is not None else None,
                    "store": item.store.value,
                    "resolved": item.key in resolved.secrets,
                }
            )
        env_data["secrets"] = secret_rows

        # Features
        declared_features = env_service.get_features()
        feature_rows = []
        for item in declared_features:
            val = resolved.features.get(item.key)
            feature_rows.append(
                {
                    "key": item.key,
                    "value": val,
                    "store": item.store.value,
                    "resolved": item.key in resolved.features,
                }
            )
        env_data["features"] = feature_rows

        # Overrides summary
        if env_model and env_model.spec and env_model.spec.overrides:
            ov = env_model.spec.overrides
            overrides_summary: Dict[str, Any] = {}
            if ov.resources:
                overrides_summary["resources"] = [str(r.resource) for r in ov.resources]
            if ov.modules:
                overrides_summary["modules"] = [f"{m.resource}.{m.module}" for m in ov.modules]
            if ov.providers:
                overrides_summary["providers"] = [str(p.provider) for p in ov.providers]
            if ov.properties:
                overrides_summary["properties"] = ov.properties
            if ov.includes:
                overrides_summary["includes"] = [{"source": inc.source, "target": inc.target} for inc in ov.includes]
            if overrides_summary:
                env_data["overrides"] = overrides_summary

        data["environment"] = env_data
        self._output_data = data

        if self._is_console_output():
            self._print_console(data)

        # Check for unresolved values
        unresolved = [r for r in var_rows if not r["resolved"]]
        unresolved += [r for r in secret_rows if not r["resolved"]]
        unresolved += [r for r in feature_rows if not r["resolved"]]
        if unresolved:
            self._errors.append(f"{len(unresolved)} value(s) could not be resolved.")
            return False

        return True

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    _SEP = "─" * 72

    def _print_console(self, data: Dict[str, Any]) -> None:
        env = data.get("environment")
        if not env:
            return

        click.echo(f"\n🌍  Environment — {env.get('name', 'unknown')}")
        click.echo(f"  Deployment: {data['deployment']}")
        click.echo(f"  File: {data['file']}")
        click.echo(f"  {self._SEP}")

        # Labels
        labels = env.get("labels")
        if labels:
            click.echo("\n  Labels:")
            for k, v in labels.items():
                click.echo(f"    {k}: {v}")

        # Properties
        props = env.get("properties")
        if props:
            click.echo("\n  Properties:")
            for k, v in props.items():
                click.echo(f"    {k}: {v}")

        # Custom
        custom = env.get("custom")
        if custom:
            click.echo("\n  Custom:")
            for k, v in custom.items():
                click.echo(f"    {k}: {v}")

        # Stages
        stages = data.get("stages", [])
        if stages:
            click.echo(f"\n  Stages ({len(stages)}):")
            for s in stages:
                scope = f" [{s['scope']}]" if s.get("scope") else ""
                deps = f" → depends: {', '.join(s['depends_on'])}" if s.get("depends_on") else ""
                click.echo(f"    • {s['name']}  ({s['provisioner']}){scope}{deps}")

        # Variables
        var_rows = env.get("variables", [])
        if var_rows:
            click.echo(f"\n  Variables ({len(var_rows)}):")
            col_key = max(len(r["key"]) for r in var_rows)
            for r in var_rows:
                status = r["value"] if r["resolved"] else click.style("⚠ unresolved", fg="yellow")
                click.echo(f"    {r['key']:<{col_key}}  = {status}")

        # Secrets
        secret_rows = env.get("secrets", [])
        if secret_rows:
            click.echo(f"\n  Secrets ({len(secret_rows)}):")
            col_key = max(len(r["key"]) for r in secret_rows)
            for r in secret_rows:
                status = r["value"] if r["resolved"] else click.style("⚠ unresolved", fg="yellow")
                click.echo(f"    {r['key']:<{col_key}}  = {status}")

        # Features
        feature_rows = env.get("features", [])
        if feature_rows:
            click.echo(f"\n  Features ({len(feature_rows)}):")
            col_key = max(len(r["key"]) for r in feature_rows)
            for r in feature_rows:
                val = r["value"]
                if val is True:
                    display = click.style("✓ enabled", fg="green")
                elif val is False:
                    display = click.style("✗ disabled", fg="red")
                else:
                    display = click.style("⚠ unresolved", fg="yellow")
                click.echo(f"    {r['key']:<{col_key}}  {display}")

        # Overrides
        overrides = env.get("overrides")
        if overrides:
            click.echo("\n  Overrides:")
            if "resources" in overrides:
                click.echo(f"    Resources: {', '.join(overrides['resources'])}")
            if "modules" in overrides:
                click.echo(f"    Modules: {', '.join(overrides['modules'])}")
            if "providers" in overrides:
                click.echo(f"    Providers: {', '.join(overrides['providers'])}")
            if "includes" in overrides:
                click.echo(f"    Includes: {len(overrides['includes'])} file(s)")

        click.echo(f"\n  {self._SEP}")
        click.echo("")
