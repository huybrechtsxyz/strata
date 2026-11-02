#!/usr/bin/env python3
"""Credentials for fetching a remote at build time (docs/work/remotes-at-scale.md, C5).

`resolve_remote()` can already authenticate a git clone from an `AuthenticationModel` plus the
values its key references resolve to, but a build has no resolved values (`build run` renders
without them) and nothing looked up the Integration document a remote or asset names.
`RemoteCredentialResolver` does both, once per build:

- it turns an Integration document into `spec.authentication`, strictly: an unknown, disabled or
  non-`sources` integration, or an authentication method that cannot authenticate git, is an
  error rather than a silent fall back to ambient credentials;
- it resolves only the keys that authentication references, through `resolve_values()`. Unless
  `--resolve` was given (`allow_stores=False`) those keys must be declared with a store `build run`
  may read without a network call (`is_direct_store()`: constant, environment variable, ...), which
  keeps the contract that a plain build makes no network call beyond fetching sources.

Nothing here writes a value anywhere: the resolved values go to `prepare_git_credentials()`, which
puts them in the git subprocess environment and cleans up its own temp files. `redact()` masks them
in any text that might echo one.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from typing import cast

from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import (
    is_direct_store,
    reachable_environments,
    resolve_deployment,
    resolve_values,
)
from strata.integrations.git_push import FETCH_METHODS, credential_keys
from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import PlatformKind
from strata.models.integration_model import Capability, IntegrationModel
from strata.services.environment_service import merge_environment_models
from strata.utils.errors import SystemError

#: A value shorter than this is not masked: it would corrupt unrelated text and is no secret.
_MIN_REDACT_LENGTH = 6


class RemoteCredentialError(SystemError):
    """Credentials for a fetch could not be resolved, so the remote cannot be fetched."""


@dataclass(frozen=True)
class FetchCredentials:
    """What `resolve_remote()` needs to authenticate a clone; the default is ambient git credentials."""

    auth: AuthenticationModel | None = None
    values: Mapping[str, str] | None = None


AMBIENT = FetchCredentials()


class RemoteCredentialResolver:
    """Resolves the Integration an asset or remote names into `FetchCredentials`, lazily and cached.

    One instance per `build run`. Nothing is looked up until `for_integration()` is called, and a
    call with no integration name does nothing at all.
    """

    def __init__(
        self, context: SolutionContext, deployment_name: str, *, allow_stores: bool, pin: str | None = None
    ) -> None:
        self._context = context
        self._deployment_name = deployment_name
        self._allow_stores = allow_stores
        self._pin = pin
        self._cache: dict[str, FetchCredentials] = {}
        self._secrets: set[str] = set()

    def for_integration(self, name: str | None, *, owner: str) -> FetchCredentials:
        """The credentials for integration `name`, or ambient when `name` is `None`.

        `owner` labels the asset or remote that asked, for error messages only.

        Raises:
            RemoteCredentialError: see the module docstring.
        """
        if not name:
            return AMBIENT
        cached = self._cache.get(name)
        if cached is None:
            cached = self._cache[name] = self._resolve(name, owner)
        return cached

    def redact(self, text: str) -> str:
        """`text` with every resolved credential value replaced by `***`."""
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, "***")
        return text

    def _resolve(self, name: str, owner: str) -> FetchCredentials:
        index = self._context.controller.index
        entry = index.get(PlatformKind.INTEGRATION, name)
        if entry is None:
            close = get_close_matches(name, sorted(index.names_of(PlatformKind.INTEGRATION)), n=1, cutoff=0.6)
            hint = f" (did you mean '{close[0]}'?)" if close else ""
            raise RemoteCredentialError(f"{owner}: integration '{name}' not found{hint}")
        spec = cast(IntegrationModel, entry.model).spec
        if not spec.enabled:
            raise RemoteCredentialError(f"{owner}: integration '{name}' is disabled (spec.enabled: false)")
        if Capability.SOURCES not in spec.capabilities:
            declared = ", ".join(sorted(spec.capabilities)) or "none"
            raise RemoteCredentialError(
                f"{owner}: integration '{name}' does not declare the 'sources' capability (declares: {declared})"
            )

        auth = spec.authentication
        if auth is None or auth.method == "cli":
            return AMBIENT
        if auth.method not in FETCH_METHODS:
            raise RemoteCredentialError(
                f"{owner}: integration '{name}' uses authentication method '{auth.method}', which cannot "
                f"authenticate a git fetch (supported: {', '.join(FETCH_METHODS)})"
            )

        keys = credential_keys(auth)
        self._check_declared(keys, name, owner)
        result = resolve_values(self._context, self._deployment_name, list(keys), version_pin=self._pin)
        failures = [d.message for d in result.diagnostics.errors if d.location in keys]
        missing = [key for key in keys if key not in result.values]
        if failures or missing:
            detail = "; ".join(failures) or f"no value for {', '.join(missing)}"
            raise RemoteCredentialError(f"{owner}: could not resolve credentials for integration '{name}': {detail}")

        values = {key: result.values[key] for key in keys}
        self._secrets.update(value for value in values.values() if len(value) >= _MIN_REDACT_LENGTH)
        return FetchCredentials(auth=auth, values=values)

    def _check_declared(self, keys: tuple[str, ...], name: str, owner: str) -> None:
        """Every key must be declared, and (without `--resolve`) in a store a plain build may read."""
        deployment = resolve_deployment(self._context, self._deployment_name, version_pin=self._pin)
        variables, secrets, features = merge_environment_models(reachable_environments(self._context, deployment))
        for key in keys:
            # Same precedence `resolve_values()` applies when a key is declared in more than one store kind.
            store = secrets.get(key) or features.get(key) or variables.get(key)
            if store is None:
                raise RemoteCredentialError(
                    f"{owner}: '{key}' (integration '{name}') is not declared in any environment "
                    f"reachable from deployment '{self._deployment_name}'"
                )
            if not self._allow_stores and not is_direct_store(store):
                raise RemoteCredentialError(
                    f"{owner}: '{key}' (integration '{name}') is stored in '{store.store.value}'; build only "
                    "reads constant/environment stores unless you pass --resolve"
                )
