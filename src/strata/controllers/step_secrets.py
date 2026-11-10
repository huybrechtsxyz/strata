#!/usr/bin/env python3
"""Which declared secrets a provisioning step used (docs/design/deployment-documentation.md, section 1).

Worked out from the step's real input at deploy time, not from the YAML afterwards. Per delivery path:

- `${secret:KEY}` tokens in the backend configuration (every tool that has one).
- A tool that takes the strata payload (`ENV_VAR_PREFIX`, Terraform and OpenTofu): tokens in the step's own
  payload, plus every secret whose key is a `variable` the root module declares. `deploy run` sets
  `TF_VAR_<key>` for every resolved value, and Terraform reads one only for a declared variable.
- A script: tokens in `script.args`, plus the keys `script.values` lists. Without `script.values`, `STRATA_VALUES`
  holds every resolved value, so the step counts as using every secret.
- A GitOps step: tokens in the remote integration's authentication.
- A Helm or Compose step (`container_secret_keys()`): tokens in what each namespace renders, scanned by the
  integration's own `referenced_secret_keys()`.
- An `output.template`: the `secrets` keys it reads (`template_secret_keys()`); dynamic access counts as every
  secret.

The answer errs towards more, never fewer: a secret a step receives but might not read is listed, so the handover
document never leaves out a secret the step had.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from strata.controllers.deployment_report_store import SecretUse
from strata.integrations.capabilities import InfraIntegration
from strata.integrations.resolved_context import ResolvedModule
from strata.models.namespace_model import NamespaceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.templater import template_secret_keys
from strata.utils.terraform_outputs import read_declared_variables
from strata.utils.value_tokens import referenced_secret_keys


def step_secret_keys(
    *,
    provisioner: ProvisionerModel,
    integration: InfraIntegration,
    path: Path,
    declared: set[str],
    root: Path,
    payload: Any = None,
    auth: Any = None,
) -> tuple[set[str], bool]:
    """Return `(keys, all_secrets)`: the declared secret keys a non-container step used, and whether it
    receives every declared secret (then `keys` is all of `declared`).

    Args:
        provisioner: The step's provisioner.
        integration: The step's resolved integration.
        path: The step's rendered root (the build directory the tool runs in).
        declared: Every secret key declared in the deployment's reachable environments.
        root: The solution root, for `output.template`.
        payload: The step's own strata payload (Terraform-style tools only).
        auth: The GitOps remote integration's authentication model, when there is one.
    """
    every = False
    keys: set[str] = set()
    if provisioner.backend is not None:
        keys |= referenced_secret_keys(provisioner.backend.configuration)
    if integration.ENV_VAR_PREFIX is not None:
        keys |= referenced_secret_keys(payload)
        keys |= read_declared_variables(path) & declared
    if provisioner.script is not None:
        keys |= referenced_secret_keys(list(provisioner.script.args or []))
        if provisioner.script.values is None:
            every = True
        else:
            keys |= set(provisioner.script.values)
    if auth is not None:
        dump = auth.model_dump(mode="json") if hasattr(auth, "model_dump") else auth
        keys |= referenced_secret_keys(dump)
    if provisioner.output is not None and provisioner.output.template:
        template_path = root / provisioner.output.template
        try:
            used = template_secret_keys(template_path.read_text(encoding="utf-8"))
        except OSError:
            used = None
        if used is None:
            every = True
        else:
            keys |= used
    if every:
        return set(declared), True
    return keys & declared, False


def container_secret_keys(
    integration: InfraIntegration,
    targets: Iterable[tuple[NamespaceModel, list[ResolvedModule]]],
    declared: set[str],
) -> tuple[set[str], bool]:
    """The declared secrets a Helm or Compose step's namespaces use, and whether that is every secret (an
    integration whose scan returns `None`). Scans the rendered modules, so call it before `deploy_namespace()`,
    which may rewrite them with the tokens resolved.
    """
    keys: set[str] = set()
    for namespace, modules in targets:
        if not modules:
            continue
        found = integration.referenced_secret_keys(namespace, modules)
        if found is None:
            return set(declared), True
        keys |= found
    return keys & declared, False


def secret_uses(keys: set[str], records: dict[str, dict[str, Any]]) -> list[SecretUse]:
    """`keys` as report-record entries, from `value_controller.secret_records()`'s per-secret details."""
    return [
        SecretUse(
            key=key,
            store=records[key]["store"],
            location=records[key]["location"],
            description=records[key]["description"],
            declared_in=records[key]["declared_in"],
        )
        for key in sorted(keys)
        if key in records
    ]
