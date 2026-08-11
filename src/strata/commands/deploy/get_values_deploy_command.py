"""Command to retrieve full resolved values for specific keys from a deployment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.services.deployment_service import DeploymentService


class GetValuesDeployCommand(BaseDeployCommand):
    """Retrieve the full resolved value for one or more keys.

    Unlike ``strata values list``, this reveals secrets in full.
    Provide one or more KEY arguments to look up.

    Exit codes:
      0 — all keys found and resolved
      3 — one or more keys missing or failed to resolve
    """

    OPERATION = "deploy_values_get"

    def __init__(
        self,
        file: Optional[str] = None,
        keys: Optional[List[str]] = None,
        work_path: Optional[str] = None,
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
        self._keys: List[str] = list(keys or [])

    def _load_related_services(self, deployment_service: DeploymentService, repo_map: Dict[str, str]) -> bool:
        """ADR-0026: only the merged environment is needed — never the workspace."""
        return self._load_environment_related_services(deployment_service, repo_map)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _execute(self) -> bool:
        if not self._keys:
            self._errors.append("No keys specified. Provide at least one KEY argument.")
            return False

        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        env_service = self._deployment_service.get_environment_service()
        if env_service is None:
            if self._is_console_output():
                click.echo("\n⚠️   No environment attached to this deployment.\n")
            self._output_data = {"file": str(self._file_path), "results": {}}
            return False

        # Resolve all values in one pass — cheaper than resolving individually
        _, resolved, _ = self._resolve_values(strict=False)

        # Merge all resolved maps into one lookup: variables + secrets + features
        all_resolved: Dict[str, Any] = {}
        all_resolved.update(resolved.variables)
        all_resolved.update({k: str(v).lower() if v is not None else "none" for k, v in resolved.features.items()})
        all_resolved.update(resolved.secrets)  # secrets last so they win on collision

        results: Dict[str, Any] = {}
        any_failed = False

        for key in self._keys:
            if key in all_resolved:
                results[key] = all_resolved[key]
            else:
                # Check if the key was declared but failed to resolve
                declared_keys = (
                    {v.key for v in env_service.get_variables()}
                    | {s.key for s in env_service.get_secrets()}
                    | {f.key for f in env_service.get_features()}
                )
                if key in declared_keys:
                    # It was declared but resolution failed — surface the error
                    err_msg = next(
                        (e for e in resolved.errors if f"'{key}'" in e),
                        f"Key '{key}' declared but could not be resolved.",
                    )
                    results[key] = f"ERROR: {err_msg}"
                else:
                    results[key] = "NOT FOUND"
                any_failed = True

        self._output_data = {"file": str(self._file_path), "results": results}

        if self._is_console_output():
            self._print_console(results)

        if any_failed:
            missing = [
                k
                for k, v in results.items()
                if isinstance(v, str) and v in ("NOT FOUND",) or (isinstance(v, str) and v.startswith("ERROR:"))
            ]
            self._errors.extend([f"Key '{k}': {results[k]}" for k in missing])
            return False

        return True

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_console(self, results: Dict[str, Any]) -> None:
        col_key = max((len(k) for k in results), default=3)
        col_key = max(col_key, len("KEY"))

        click.echo("")
        for key, value in results.items():
            click.echo(f"  {key:<{col_key}}  {value}")
        click.echo("")
