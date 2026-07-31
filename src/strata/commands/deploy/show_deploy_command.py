"""Command to show resolved deployment configuration: remote versions, workspace, and environment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ValueController


def _mask(value: Any) -> str:
    """Mask a secret value for safe display."""
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 4)


class ShowDeployCommand(BaseDeployCommand):
    """Show resolved deployment configuration for a deployment manifest.

    For each remote configured in the workspace, displays:
    - The effective reference (after applying environment overrides)
    - Whether the reference came from an environment override or the workspace default

    Also prints the workspace and environment files in use, the deployment's
    stage list, and the full resolved environment: meta, properties, custom
    settings, resolved variables (full values), resolved secrets (masked),
    resolved feature flags, and an overrides summary.
    """

    OPERATION = "deploy_show"

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
        self._resolved_remotes: List[Dict[str, str]] = []

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        ok = self._collect()
        if self._is_console_output():
            self._print_output()
        return ok

    # -------------------------------------------------------------------------
    # Implementation
    # -------------------------------------------------------------------------

    def _collect(self) -> bool:
        """Resolve remote references and populate self._output_data."""
        if self._deployment_service is None or self._configuration_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        # Determine which remotes have an environment-level override
        env_override_names: set[str] = set()
        env_name: Optional[str] = None
        env_path: Optional[str] = None
        ws_path: Optional[str] = None

        env_service = None
        try:
            env_service = self._deployment_service.get_environment_service()
            if env_service:
                env_name = env_service.get_name()
                env_path = str(env_service.path) if env_service.path else None
                spec = env_service.model.spec if env_service.model else None
                if spec and spec.overrides and spec.overrides.remotes:
                    for r in spec.overrides.remotes:
                        env_override_names.add(str(r.remote))
        except Exception:
            pass

        ws = self._deployment_service._workspace_service
        if ws:
            ws_path = str(ws.path) if ws.path else None

        # Build remote rows — config_remote.reference is already the effective ref
        # because apply_environment_overrides() mutated it in-place before we get here
        remotes_out: List[Dict[str, str]] = []
        config_model = self._configuration_service.model
        if config_model and config_model.spec and config_model.spec.remotes:
            for remote in config_model.spec.remotes:
                name = str(remote.name)
                effective_ref = str(remote.reference) if remote.reference else "(none)"
                if name in env_override_names:
                    source = f"{env_name} (override)" if env_name else "env override"
                else:
                    source = "workspace default"
                remotes_out.append(
                    {
                        "name": name,
                        "reference": effective_ref,
                        "source": source,
                    }
                )

        self._resolved_remotes = remotes_out

        # --- Deployment stage list ---
        deployment_model = self._deployment_service.model
        all_stages = deployment_model.spec.stages or [] if deployment_model else []
        stage_rows: List[Dict[str, Any]] = []
        for s in all_stages:
            row: Dict[str, Any] = {
                "name": str(s.name),
                "provisioner": s.provisioner or "terraform",
                "scope": s.scope or None,
            }
            if s.depends_on:
                row["depends_on"] = [str(d) for d in s.depends_on]
            stage_rows.append(row)

        # --- Full resolved environment (meta/properties/values/overrides) ---
        environment_detail, env_resolved_ok = self._collect_environment(env_service)

        self._output_data: Dict[str, Any] = {
            "file": str(self._file_path),
            "deployment": self._deployment_service.get_name(),
            "workspace": ws_path,
            "environment": env_name,
            "environment_file": env_path,
            "remotes": remotes_out,
            "stages": stage_rows,
            "environment_detail": environment_detail,
        }
        return env_resolved_ok

    def _collect_environment(self, env_service: Any) -> tuple[Optional[Dict[str, Any]], bool]:
        """Build the full resolved-environment payload (meta, properties, values, overrides).

        Returns ``(data, ok)`` where ``ok`` is False when one or more declared
        variables/secrets/features could not be resolved.
        """
        if env_service is None:
            return None, True

        env_model = env_service.model
        env_data: Dict[str, Any] = {}

        if env_model and env_model.meta:
            env_data["name"] = str(env_model.meta.name)
            if env_model.meta.labels:
                env_data["labels"] = dict(env_model.meta.labels)
            if env_model.meta.annotations:
                env_data["annotations"] = dict(env_model.meta.annotations)

        if env_model and env_model.spec:
            if env_model.spec.properties:
                env_data["properties"] = dict(env_model.spec.properties)
            if env_model.spec.custom:
                env_data["custom"] = dict(env_model.spec.custom)

        controller = ValueController()
        assert self._deployment_service is not None
        _, resolved, _ = controller.resolve_values(self._deployment_service, strict=False)

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

        unresolved = [r for r in var_rows if not r["resolved"]]
        unresolved += [r for r in secret_rows if not r["resolved"]]
        unresolved += [r for r in feature_rows if not r["resolved"]]
        if unresolved:
            self._errors.append(f"{len(unresolved)} value(s) could not be resolved.")
            return env_data, False

        return env_data, True

    def _print_output(self) -> None:
        """Render deployment show summary to console."""
        ds = self._deployment_service
        if ds is None:
            return

        click.echo(f"\n📋  Deployment:   {ds.get_name()}")
        click.echo(f"    File:         {self._file_path}")

        ws = ds._workspace_service
        if ws:
            click.echo(f"    Workspace:    {ws.path}")

        try:
            env = ds.get_environment_service()
            if env:
                label = f"{env.get_name()} ({env.path})" if env.path else env.get_name()
                click.echo(f"    Environment:  {label}")
        except Exception:
            pass

        if not self._resolved_remotes:
            click.echo("\n    (no remotes configured)\n")
        else:
            click.echo("\n    Remote Versions:\n")
            name_w = max(len(r["name"]) for r in self._resolved_remotes)
            ref_w = max(len(r["reference"]) for r in self._resolved_remotes)
            click.echo(f"    {'Remote':<{name_w}}  {'Effective Ref':<{ref_w}}  Source")
            click.echo("    " + "─" * (name_w + ref_w + 18))
            for r in self._resolved_remotes:
                click.echo(f"    {r['name']:<{name_w}}  {r['reference']:<{ref_w}}  {r['source']}")
            click.echo()

        self._print_environment_detail()

    # -------------------------------------------------------------------------
    # Console output — resolved environment detail
    # -------------------------------------------------------------------------

    _SEP = "─" * 72

    def _print_environment_detail(self) -> None:
        env = self._output_data.get("environment_detail")
        stages = self._output_data.get("stages", [])

        if stages:
            click.echo(f"    Stages ({len(stages)}):")
            for s in stages:
                scope = f" [{s['scope']}]" if s.get("scope") else ""
                deps = f" → depends: {', '.join(s['depends_on'])}" if s.get("depends_on") else ""
                click.echo(f"      • {s['name']}  ({s['provisioner']}){scope}{deps}")
            click.echo()

        if not env:
            return

        click.echo(f"  {self._SEP}")
        click.echo(f"  🌍  Resolved environment — {env.get('name', 'unknown')}")

        labels = env.get("labels")
        if labels:
            click.echo("\n  Labels:")
            for k, v in labels.items():
                click.echo(f"    {k}: {v}")

        props = env.get("properties")
        if props:
            click.echo("\n  Properties:")
            for k, v in props.items():
                click.echo(f"    {k}: {v}")

        custom = env.get("custom")
        if custom:
            click.echo("\n  Custom:")
            for k, v in custom.items():
                click.echo(f"    {k}: {v}")

        var_rows = env.get("variables", [])
        if var_rows:
            click.echo(f"\n  Variables ({len(var_rows)}):")
            col_key = max(len(r["key"]) for r in var_rows)
            for r in var_rows:
                status = r["value"] if r["resolved"] else click.style("⚠ unresolved", fg="yellow")
                click.echo(f"    {r['key']:<{col_key}}  = {status}")

        secret_rows = env.get("secrets", [])
        if secret_rows:
            click.echo(f"\n  Secrets ({len(secret_rows)}):")
            col_key = max(len(r["key"]) for r in secret_rows)
            for r in secret_rows:
                status = r["value"] if r["resolved"] else click.style("⚠ unresolved", fg="yellow")
                click.echo(f"    {r['key']:<{col_key}}  = {status}")

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
