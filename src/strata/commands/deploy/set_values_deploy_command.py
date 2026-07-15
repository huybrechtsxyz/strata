"""Command to write a value to its configured store backend."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ValueController
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
)


class SetValuesDeployCommand(BaseDeployCommand):
    """Write a value to the store backend configured for the given key.

    Behaviour depends on the store type:

    - **constant**: Cannot write — prints the file path and key location.
    - **environment**: Prints the env var name to set.
    - **github**: Calls ``gh secret set`` (requires ``gh`` CLI).
    - **Integration-backed** (azure-keyvault, bitwarden, vault, consul,
      azure-appconfig, infisical, etcd, flagsmith): Calls the integration's
      ``set_variable``, ``set_secret``, or ``set_feature`` method.

    Exit codes:
      0 — value written successfully (or instruction printed for constant/env)
      3 — key not found or write failed
    """

    OPERATION = "deploy_values_set"

    def __init__(
        self,
        file: Optional[str] = None,
        key: Optional[str] = None,
        value: Optional[str] = None,
        from_file: Optional[str] = None,
        from_stdin: bool = False,
        work_path: Optional[str] = None,
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
        self._key = key
        self._raw_value = value
        self._from_file = from_file
        self._from_stdin = from_stdin

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _execute(self) -> bool:
        if not self._key:
            self._errors.append("No key specified. Use --key.")
            return False

        # Resolve the actual value to write
        resolved_value = self._resolve_input_value()
        if resolved_value is None:
            return False

        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        env_service = self._deployment_service.get_environment_service()
        if env_service is None:
            self._errors.append("No environment attached to this deployment.")
            return False

        # Find the key in variables, secrets, or features
        item, item_type = self._find_item(env_service)
        if item is None:
            self._errors.append(f"Key '{self._key}' not found in environment variables, secrets, or features.")
            return False

        # Dispatch based on store type
        ok, message = self._dispatch_set(item, item_type, resolved_value)

        self._output_data = {
            "file": str(self._file_path),
            "key": self._key,
            "store": str(item.store.value),  # type: ignore[union-attr]
            "type": item_type,
            "success": ok,
            "message": message,
        }

        if self._is_console_output():
            if ok:
                click.echo(f"\n✅  {message}\n")
            else:
                click.echo(f"\n❌  {message}\n")

        if not ok:
            self._errors.append(message)

        return ok

    # ------------------------------------------------------------------
    # Input resolution
    # ------------------------------------------------------------------

    def _resolve_input_value(self) -> Optional[str]:
        """Resolve the value from --value, --from-file, or --stdin."""
        sources = sum(
            [
                self._raw_value is not None,
                self._from_file is not None,
                self._from_stdin,
            ]
        )
        if sources == 0:
            self._errors.append("No value provided. Use --value, --from-file, or --stdin.")
            return None
        if sources > 1:
            self._errors.append("Multiple value sources specified. Use only one of --value, --from-file, or --stdin.")
            return None

        if self._raw_value is not None:
            return self._raw_value

        if self._from_file is not None:
            path = Path(self._from_file)
            if not path.exists():
                self._errors.append(f"File not found: {self._from_file}")
                return None
            try:
                return path.read_text(encoding="utf-8")
            except OSError as exc:
                self._errors.append(f"Cannot read file: {exc}")
                return None

        # stdin
        if sys.stdin.isatty():
            self._errors.append("No data on stdin. Pipe a value or use --value / --from-file.")
            return None
        return sys.stdin.read()

    # ------------------------------------------------------------------
    # Key lookup
    # ------------------------------------------------------------------

    def _find_item(
        self, env_service: Any
    ) -> Tuple[Optional[Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel]], str]:
        """Find the key in variables, secrets, or features. Returns (item, type_name)."""
        for var in env_service.get_variables():
            if var.key == self._key:
                return var, "variable"

        for secret in env_service.get_secrets():
            if secret.key == self._key:
                return secret, "secret"

        for feature in env_service.get_features():
            if feature.key == self._key:
                return feature, "feature"

        return None, ""

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch_set(
        self,
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        item_type: str,
        value: str,
    ) -> Tuple[bool, str]:
        """Dispatch the set operation based on store type. Returns (success, message)."""
        store = item.store

        # --- constant: cannot write, show file location ---
        if store.value == "constant":  # type: ignore[union-attr]
            return self._handle_constant(item, item_type)

        # --- environment: print instruction ---
        if store.value == "environment":  # type: ignore[union-attr]
            return self._handle_environment(item, value)

        # --- github: use gh CLI ---
        if isinstance(item, SecretStoreModel) and store == SecretStoreType.GITHUB:
            return self._handle_github(item, value)

        # --- integration-backed stores ---
        return self._handle_integration(item, item_type, value)

    def _handle_constant(
        self,
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        item_type: str,
    ) -> Tuple[bool, str]:
        """Constant values live in the YAML file — tell the user where."""
        env_service = self._deployment_service.get_environment_service()  # type: ignore[union-attr]
        file_path = env_service.path if env_service else "unknown"
        return True, (
            f"Key '{self._key}' uses store 'constant' — edit directly in:\n"
            f"       File: {file_path}\n"
            f"       Type: {item_type}\n"
            f"       Current value: {item.value}"
        )

    def _handle_environment(
        self,
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        value: str,
    ) -> Tuple[bool, str]:
        """Environment store — print the env var instruction."""
        env_var = str(item.value)
        return True, (
            f"Key '{self._key}' reads from env var '{env_var}'.\n"
            f"       Set it with:\n"
            f'         export {env_var}="{value}"\n'
            f"       Or on Windows:\n"
            f'         $env:{env_var} = "{value}"'
        )

    def _handle_github(self, item: SecretStoreModel, value: str) -> Tuple[bool, str]:
        """GitHub secrets — use gh CLI."""
        import subprocess

        secret_name = str(item.value).upper()
        try:
            result = subprocess.run(
                ["gh", "secret", "set", secret_name, "--body", value],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, f"GitHub secret '{secret_name}' updated successfully."
            return False, f"gh secret set failed: {result.stderr or result.stdout}"
        except FileNotFoundError:
            return False, (
                f"GitHub CLI (gh) not found. Set the secret manually:\n"
                f'       gh secret set {secret_name} --body "<value>"'
            )
        except Exception as exc:
            return False, f"Failed to set GitHub secret: {exc}"

    def _handle_integration(
        self,
        item: Union[VariableStoreModel, SecretStoreModel, FeatureStoreModel],
        item_type: str,
        value: str,
    ) -> Tuple[bool, str]:
        """Integration-backed store — dispatch to set_variable/set_secret/set_feature."""
        store_type = item.store.value  # type: ignore[union-attr]
        reference = str(item.value)

        # Initialize integrations
        ValueController._ensure_integrations_initialized()
        integration = ValueController._get_integration_by_type(store_type)

        if integration is None:
            return False, (
                f"No integration registered for store type '{store_type}'. Check your workspace configuration."
            )

        # Check availability
        available, err = integration.ensure_available()
        if not available:
            return False, f"Integration '{store_type}' not available: {err}"

        # Dispatch to the appropriate set method
        try:
            if item_type == "variable":
                ok = integration.set_variable(reference, value)
            elif item_type == "secret":
                ok = integration.set_secret(reference, value)
            elif item_type == "feature":
                # Features are booleans
                bool_val = value.lower() not in ("0", "false", "no", "off", "")
                ok = integration.set_feature(reference, bool_val)
            else:
                return False, f"Unknown item type: {item_type}"

            if ok:
                return True, (f"Key '{self._key}' updated in '{store_type}' store (reference: {reference}).")
            return False, (
                f"Integration '{store_type}' returned failure for key '{self._key}' "
                f"(reference: {reference}). Check integration logs for details."
            )
        except Exception as exc:
            return False, f"Integration '{store_type}' error: {exc}"
