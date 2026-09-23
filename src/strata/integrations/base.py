#!/usr/bin/env python3
"""The `Integration` base class (ADR-0021 D1) — identity, configuration, and
every transport an integration may use.

**Capability is the class contract; transport is runtime configuration.**
`consul kv get my/key` and `GET /v1/kv/my/key` are the same capability
(resolve a value) over two different transports, and one workspace may have
the CLI configured while another only has the service API — so which
transport is used cannot be a property of the class (see the capability
ABCs in `capabilities.py` for the contract half of the split). This class
owns both transports; a concrete integration dispatches on `self.transport`.

A `config: IntegrationModel | None` is optional throughout — the three store
resolvers already built work from environment variables alone with no
`Integration` document, and a CLI tool works from `PATH` alone the same way.
`config` only matters once a solution actually declares one (to override the
executable, pin a version, point at a networked transport, ...).
"""

import shutil
from abc import ABC
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from packaging.specifiers import SpecifierSet

from strata.integrations.errors import IntegrationError
from strata.models.integration_model import IntegrationModel
from strata.utils.transport import (
    EXECUTABLE_NOT_FOUND,
    NO_RESPONSE,
    CommandResult,
    HttpResult,
    http_request,
    run_command,
)


class Integration(ABC):
    """Identity, configuration, and every transport an integration may use."""

    #: The `type` string this class registers under (e.g. "terraform",
    #: "consul", "infisical") — matches `ProvisionerModel.tool`/a store's
    #: `store` field (ADR-0021 D2).
    TYPE: ClassVar[str]

    #: Core capabilities (see `integration_model.VALID_INTEGRATION_CAPABILITIES`)
    #: plus, in principle, `x-`-prefixed ones — though a Python class
    #: declaring its own capabilities has no reason to declare an extension
    #: it doesn't act on, so this is realistically core-only in practice.
    CAPABILITIES: ClassVar[frozenset[str]]

    #: Transports this class can speak. An open vocabulary (ADR-0021 D1) —
    #: a review of v1's ~36 integrations found `cli`/`api` alone false
    #: (syslog uses a raw socket, OTel/etcd use gRPC, the Azure resolvers use
    #: an SDK client). Conventional values: "cli", "http", "sdk", "socket",
    #: "grpc".
    TRANSPORTS: ClassVar[frozenset[str]]

    #: Default executable name for the "cli" transport. `None` when this
    #: class has no CLI form at all. Overridable per-document via
    #: `spec.command` (see `command` below) — needed for a generic wrapper
    #: whose binary isn't known until the document supplies it.
    COMMAND: ClassVar[str | None] = None

    #: Arguments that print this tool's version, for `get_version()`.
    #: Overridden per-class — CLIs disagree on the flag (`--version`,
    #: `version`, `-version`, ...).
    VERSION_ARGS: ClassVar[tuple[str, ...]] = ("--version",)

    def __init__(self, config: IntegrationModel | None = None) -> None:
        """
        Args:
            config: The `Integration` document configuring this instance,
                when the solution declares one. `None` for a built-in
                working from PATH/environment variables alone.

        Raises:
            IntegrationError: `config.spec.type` doesn't match `self.TYPE` —
                a registry/caller bug, not a document authoring error.
        """
        if config is not None and config.spec.type != self.TYPE:
            raise IntegrationError(
                f"{type(self).__name__} is registered for type '{self.TYPE}', "
                f"but was configured with an Integration document of type '{config.spec.type}'."
            )
        self.config = config
        self._transport: str | None = None

    @property
    def name(self) -> str:
        """The declaration name for messages — the document's, or the type when there is none."""
        return self.config.meta.name if self.config is not None else self.TYPE

    @property
    def command(self) -> str | None:
        """The executable for the "cli" transport.

        Resolution order (ADR-0021 D6): `spec.command` override, else this
        class's own `COMMAND`, else the document's `type` (a generic wrapper
        whose binary matches its declared type name). `None` only when none
        of the three apply — a class with no CLI form and no document.
        """
        if self.config is not None and self.config.spec.command:
            return self.config.spec.command
        if self.COMMAND is not None:
            return self.COMMAND
        if self.config is not None:
            return self.config.spec.type
        return None

    @property
    def transport(self) -> str:
        """The transport this call uses.

        Resolution order (ADR-0021 D1): the document's explicit
        `spec.transport` (validated against this class's own `TRANSPORTS`,
        never a global enum); the sole supported transport when there is
        only one; otherwise `"cli"` when it's supported and the command is
        on `PATH`; otherwise whatever's left, deterministically ordered.

        Cached after first resolution — `PATH` does not change mid-process,
        and `is_available()` shells out.

        Raises:
            IntegrationError: `spec.transport` names something this class
                does not support.
        """
        if self._transport is not None:
            return self._transport

        configured = self.config.spec.transport if self.config is not None else None
        if configured is not None:
            if configured not in self.TRANSPORTS:
                raise IntegrationError(
                    f"{self.name}: transport '{configured}' is not supported. "
                    f"Supported: {sorted(self.TRANSPORTS)}."
                )
            self._transport = configured
        elif len(self.TRANSPORTS) == 1:
            self._transport = next(iter(self.TRANSPORTS))
        elif "cli" in self.TRANSPORTS and self.is_available():
            self._transport = "cli"
        else:
            remaining = sorted(self.TRANSPORTS - {"cli"}) or sorted(self.TRANSPORTS)
            self._transport = remaining[0]
        return self._transport

    # ------------------------------------------------------------------
    # cli transport
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True when `command` resolves to something on `PATH`."""
        command = self.command
        return command is not None and shutil.which(command) is not None

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 300,
    ) -> CommandResult:
        """Run `command` with `args` (see `strata.utils.transport.run_command`)."""
        command = self.command
        if command is None:
            return CommandResult(
                returncode=EXECUTABLE_NOT_FOUND,
                stdout="",
                stderr=f"{self.name}: no command configured for the 'cli' transport",
            )
        return run_command([command, *args], cwd=cwd, env=env, timeout=timeout)

    # ------------------------------------------------------------------
    # networked transports (http, sdk, ...)
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: int = 30,
    ) -> HttpResult:
        """`path` joined onto `spec.endpoints.address` (see `strata.utils.transport.http_request`)."""
        endpoints = self.config.spec.endpoints if self.config is not None else None
        if endpoints is None:
            return HttpResult(
                status=NO_RESPONSE, body=f"{self.name}: no endpoint configured for a networked transport"
            )
        url = f"{endpoints.address.rstrip('/')}/{path.lstrip('/')}"
        return http_request(method, url, headers=headers, body=body, timeout=timeout)

    # ------------------------------------------------------------------
    # version
    # ------------------------------------------------------------------

    def get_version(self) -> str | None:
        """The installed version, or `None` when it can't be determined.

        Only meaningful for the "cli" transport — a networked integration's
        version is a property of the remote service, not something this
        process can query generically.
        """
        if self.transport != "cli" or not self.is_available():
            return None
        result = self.run(*self.VERSION_ARGS, timeout=10)
        if not result.is_successful:
            return None
        return self.parse_version(result.payload)

    def parse_version(self, raw: str) -> str:
        """Extract a clean version string from `VERSION_ARGS`' raw output.

        Default: the output, trimmed. Override for a real CLI whose output
        needs picking apart (e.g. Terraform's `Terraform v1.7.0` — see
        `TerraformIntegration`, ADR-0021 Phase 5). Not abstract: an API-only
        integration (no "cli" transport) never calls this at all, and
        forcing every subclass to implement a meaningless parser would be
        the wrong default.
        """
        return raw.strip()

    def ensure_version(self, expected: str | None) -> None:
        """Raise if the installed version doesn't satisfy `expected`.

        `expected` is a PEP 440 specifier (e.g. `">=1.17"`, `"~=1.9"`) —
        typically `IntegrationModel.spec.version`, the single owner of this
        fact (ADR-0021 D4). `None` means no assertion — a no-op.

        Args:
            expected: A PEP 440 version specifier, or `None`.

        Raises:
            IntegrationError: The version cannot be determined, or does not
                satisfy `expected`.
        """
        if expected is None:
            return
        version = self.get_version()
        if version is None:
            raise IntegrationError(
                f"{self.name}: could not determine the installed version to check against '{expected}'."
            )
        if not SpecifierSet(expected).contains(version):
            raise IntegrationError(f"{self.name}: version {version} does not satisfy '{expected}'.")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"
