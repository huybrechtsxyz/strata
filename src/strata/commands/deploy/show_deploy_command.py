"""Command to show resolved deployment configuration: remote versions, workspace, and environment."""

from typing import Any, Dict, List, Optional, Set

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.services.deployment_service import DeploymentService
from strata.utils.provisioner_resolution import allowed_secret_keys_for_stages
from strata.utils.resolved_values import ResolvedValues
from strata.utils.stage_selection import StageSelectionMode, evaluate_enabled


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

    ``--stage``/``--scope`` select which stages to preview, exactly as they do on
    ``deploy run``, and the secrets view narrows to what those stages would
    actually receive. Gating is *disclosed*, never applied: a disabled stage is
    still listed, marked with the reason a run would skip it (ADR-0083 D11).
    """

    OPERATION = "deploy_show"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        scope: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
        no_cache: bool = False,
        refresh_cache: bool = False,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
        )
        self._stage = stage
        self._scope = scope
        self._resolved_remotes: List[Dict[str, str]] = []

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _load_related_services(self, deployment_service: DeploymentService, repo_map: Dict[str, str]) -> bool:
        """ADR-0026: only the merged environment is needed here — never the workspace.

        Effective remote references still need the environment's declared overrides
        applied to the active ``ConfigurationService``, so ``apply_remote_overrides``
        is requested explicitly.
        """
        return self._load_environment_related_services(deployment_service, repo_map, apply_remote_overrides=True)

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

        # ADR-0026: no workspace service is loaded for this command (environment-only
        # load) — the resolved path comes from the resolved-environment cache snapshot.
        ws_path = self._environment_snapshot.get("workspace_path") if self._environment_snapshot else None

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

        # Resolved once, here, and threaded down — the stage rows need it (to evaluate
        # `enabled`) and so does the environment detail. Resolving separately in each
        # place would hit the secret stores twice, which `_resolve_values`' own cache
        # does not prevent: it caches variables and features, never secrets.
        _, resolved, _ = self._resolve_values(strict=False)

        # --- Deployment stage list ---
        # INSPECT, and load-bearing: it applies --stage/--scope without gating. DEPLOY
        # would filter disabled stages out and leave the `would_skip` marker below with
        # nothing to mark — a preview that cannot preview a skip (ADR-0083 D11).
        selection = self._resolve_stages(StageSelectionMode.INSPECT)
        if selection is None:
            return False

        stages_ok = True
        stage_rows: List[Dict[str, Any]] = []
        for s in selection.to_run:
            row: Dict[str, Any] = {
                "name": str(s.name),
                "provisioner": s.provisioner or "terraform",
                "scope": s.scope or None,
            }
            if s.depends_on:
                row["depends_on"] = [str(d) for d in s.depends_on]

            row["enabled"] = s.enabled
            _is_enabled, skip, enabled_error = evaluate_enabled(s, resolved)
            row["would_skip"] = skip is not None
            row["skip_reason"] = skip.detail if skip is not None else None
            if enabled_error:
                # An unresolvable gate is not "enabled" and not "skipped" — it is a run
                # that would abort. Reporting it is what makes this an honest preview.
                self._errors.append(enabled_error)
                stages_ok = False
            stage_rows.append(row)

        # --- Full resolved environment (meta/properties/values/overrides) ---
        # Only narrow the secrets view when the operator actually narrowed the
        # selection. Unscoped, every stage is selected and the union of their
        # allowlists would be *empty* for any deployment that declares no `secrets:`
        # at all — accurate about what a run injects, but a silent, drastic change to
        # the default view. Scoping stays opt-in, tied to the flag that asked for it.
        allowed_secrets: Optional[Set[str]] = None
        if self._stage or self._scope:
            declared = {item.key for item in env_service.get_secrets()} if env_service else set()
            allowed_secrets = allowed_secret_keys_for_stages(list(selection.to_run), declared)

        environment_detail, env_resolved_ok = self._collect_environment(env_service, resolved, allowed_secrets)

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
        return env_resolved_ok and stages_ok

    def _collect_environment(
        self,
        env_service: Any,
        resolved: ResolvedValues,
        allowed_secrets: Optional[Set[str]] = None,
    ) -> tuple[Optional[Dict[str, Any]], bool]:
        """Build the full resolved-environment payload (meta, properties, values, overrides).

        Takes *resolved* rather than resolving internally: :meth:`_collect` needs the
        same values to evaluate stage ``enabled`` gates, and a second resolution pass
        would re-read every secret store.

        *allowed_secrets* is the set of secret keys the selected stages would actually
        receive, or ``None`` for the unscoped view. Out-of-scope secrets are still
        listed — see :meth:`_secret_row`.

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
        secret_rows = [self._secret_row(item, resolved, allowed_secrets) for item in declared_secrets]
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

    @staticmethod
    def _secret_row(
        item: Any,
        resolved: ResolvedValues,
        allowed_secrets: Optional[Set[str]],
    ) -> Dict[str, Any]:
        """One secret row, with its value withheld when out of the selected stages' scope.

        An out-of-scope secret is *listed*, not omitted. Omitting it would make a
        secret that exists but is excluded indistinguishable from one that was never
        declared — and "why can't my stage see SECRET_FOO?" is exactly the question
        omission answers worst. ``in_scope: false`` answers it outright.

        Withholding the value is modelling, not a security boundary: the same operator
        sees every value by dropping ``--stage``. The row answers "what would this
        stage receive?", and for an out-of-scope secret the answer is nothing.
        """
        in_scope = allowed_secrets is None or item.key in allowed_secrets
        val = resolved.secrets.get(item.key)
        return {
            "key": item.key,
            "value": _mask(val) if (in_scope and val is not None) else None,
            "store": item.store.value,
            # Resolution is a property of the secret, not of the selection: a secret
            # that resolved fine but is out of scope must not be reported as a failure.
            "resolved": item.key in resolved.secrets,
            "in_scope": in_scope,
        }

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
                # Names the flag and what it resolved to, not just "skipped" — a marker
                # that says only "skipped" recreates the guesswork one level down.
                gate = (
                    click.style(f"  ⏭\ufe0f would skip — {s['skip_reason']}", fg="yellow")
                    if s.get("would_skip")
                    else ""
                )
                click.echo(f"      • {s['name']}  ({s['provisioner']}){scope}{deps}{gate}")
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
                if not r.get("in_scope", True):
                    status = click.style("— not in the selected stage's allowlist", fg="cyan")
                elif r["resolved"]:
                    status = r["value"]
                else:
                    status = click.style("⚠ unresolved", fg="yellow")
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
