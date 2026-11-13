#!/usr/bin/env python3
"""Service for loading and validating environment configuration."""

import re
from collections.abc import Sequence
from typing import Any

from strata.models.common_models import PlatformBaseModel
from strata.models.configuration_model import ConfigurationSecurityModel
from strata.models.deployment_model import DeploymentModel
from strata.models.environment_model import EnvironmentModel, EnvironmentTemplateModel
from strata.models.store_model import (
    FeatureStoreModel,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
    VariableStoreType,
)
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.dict_merge import deep_merge
from strata.utils.value_tokens import (
    ARTIFACT_TOKEN_FIELDS,
    DEPLOYMENT_TOKEN_KEYS,
    VALUE_TOKEN_KINDS,
    artifact_key,
    artifact_token_problem,
    deployment_token_values,
    extract_value_tokens,
    find_malformed_value_tokens,
    resolve_value_tokens,
    split_artifact_token_key,
)

#: Maps a Value token's kind to the environment store that declares it.
_STORE_BY_TOKEN_KIND = {"var": "variables", "secret": "secrets", "feature": "features"}

#: Token kinds a store's own `value` is never resolved against (only `${value:}` is).
_UNSUPPORTED_STORE_VALUE_TOKEN_KINDS = frozenset({"var", "secret", "feature", "output", "deployment", "artifact"})

#: A value that is a URL rather than a name (`validate_secret_store_values()`).
_URL_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

#: The stores merged by `key` across `extends` (`merge_environment_specs()`).
_STORE_FIELDS = ("variables", "secrets", "features")


def _replace_by_key(base: list[dict[str, Any]], child: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Base entries first; a child entry with the same `key` replaces the base's whole entry, new keys append."""
    merged: dict[Any, dict[str, Any]] = {}
    for item in [*base, *child]:
        identity = item.get("key")
        merged[identity if identity is not None else object()] = dict(item)
    return list(merged.values())


def merge_environment_specs(base: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge a base environment's spec into a child's, on dumped dicts (`base-parameters-v2.md` (work doc)).

    - `properties`, `custom`: deep-merged per leaf key, child wins.
    - `variables`, `secrets`, `features`: by `key`. A child entry replaces the base's **whole** entry, so a variable
      moved from `store: constant` to a Key Vault store keeps no stale `value`; new keys are appended. The same
      rule `merge_environment_models()` applies to a deployment's list of environments.
    - `partial`, `extends`: consumed and stripped from the result.

    Neither input is mutated.
    """
    merged: dict[str, Any] = deep_merge(base, child)
    for field in _STORE_FIELDS:
        base_items = base.get(field) or []
        child_items = child.get(field) or []
        if base_items or child_items:
            merged[field] = _replace_by_key(base_items, child_items)
    merged.pop("partial", None)
    merged.pop("extends", None)
    return merged


class EnvironmentService(BaseService[EnvironmentModel]):
    """Service for handling environment configuration.

    Carries the Phase 2 check that ADR-0002 deferred across half the schema.
    `validate_value_tokens()` (`strata.utils.value_tokens`) checks at Phase 1
    that a token is *shaped* right; only a real Environment can say whether
    its **key exists**. Models with token-bearing fields — `dns`
    (record values), `network` (CIDRs), `firewall` (rule from/to), `module`
    (service environment values) — have all been waiting for this.
    """

    def _get_model_class(self) -> type[EnvironmentModel]:
        """Return the EnvironmentModel class for validation."""
        return EnvironmentModel

    def declared_keys(self) -> dict[str, set[str]]:
        """Return the keys this environment declares, by token kind.

        Keyed by token kind (`var`/`secret`/`feature`) rather than store name,
        so callers can look up straight from a parsed token.
        """
        self._ensure_validated()
        assert self.model is not None
        spec = self.model.spec
        return {
            "var": {v.key for v in spec.variables or []},
            "secret": {s.key for s in spec.secrets or []},
            "feature": {f.key for f in spec.features or []},
        }

    def validate_document_tokens(self, model: PlatformBaseModel) -> Diagnostics:
        """Check every Value token in `model` resolves to a key this environment declares.

        Walks the document's serialized form for strings containing tokens,
        which keeps this generic — one implementation covers dns, network,
        firewall, module and anything added later, instead of each model
        growing its own traversal.

        Args:
            model: Any already-validated strata document.

        Returns:
            One error per unresolved token, each located at the field path
            where the token was written.
        """
        self._ensure_validated()
        assert self.model is not None
        return unresolved_value_tokens(model, self.declared_keys(), self.model.meta.name)

    def validate_artifact_references(self, artifact_names: set[str]) -> Diagnostics:
        """Check every `store: artifact` variable's `value` names a real
        `ArtifactModel` (docs/config/artifact.md).

        The one `(kind, name)` reference `references.py`'s generic
        `References()` field walker can't check itself:
        `VariableStoreModel.value: Any` is only *conditionally* an artifact
        reference (only when `store == artifact`; for every other store it
        means something else entirely) — the same reasoning
        `references.py`'s own docstring gives for why topology components
        are deliberately excluded there too and checked by a dedicated
        service method instead (`WorkspaceService.validate_topology_
        references()`).

        Args:
            artifact_names: Every declared `ArtifactModel.meta.name` in the
                solution.

        Returns:
            One error per `store: artifact` variable whose `value` does not
            name a real artifact.
        """
        diagnostics = Diagnostics()
        self._ensure_validated()
        assert self.model is not None
        for variable in self.model.spec.variables or []:
            if variable.store != VariableStoreType.ARTIFACT:
                continue
            artifact_name = str(variable.value)
            if artifact_name not in artifact_names:
                diagnostics.error(
                    f"Variable '{variable.key}': 'store: artifact' references unknown artifact "
                    f"'{artifact_name}'. Available: {sorted(artifact_names)}",
                    location="spec.variables",
                    code="undefined_artifact",
                )
        return diagnostics

    def validate_store_value_tokens(self) -> Diagnostics:
        """Reject a `${var:}`/`${secret:}`/`${feature:}`/`${output:}` token in a
        variable/secret/feature's own `value`.

        `resolve_values()` resolves a `store: constant` value only against
        `${value:}` references, so any other token there is accepted by the
        generic token walk (the key is declared) but never resolves: it fails
        `deploy run` with `value_resolution_failed` and reaches Terraform as the
        literal token text. Chaining one key through another would also need
        cycle detection, which `${value:}` avoids by requiring a literal target.

        Returns:
            One error per offending token, located at the store's `value`.
        """
        diagnostics = Diagnostics()
        self._ensure_validated()
        assert self.model is not None
        spec = self.model.spec
        groups: list[tuple[str, str, Sequence[VariableStoreModel | SecretStoreModel | FeatureStoreModel]]] = [
            ("Variable", "variables", spec.variables or []),
            ("Secret", "secrets", spec.secrets or []),
            ("Feature", "features", spec.features or []),
        ]
        for noun, section, stores in groups:
            for position, store in enumerate(stores):
                for _path, text in _iter_strings(store.value):
                    for kind, key in extract_value_tokens(text):
                        if kind not in _UNSUPPORTED_STORE_VALUE_TOKEN_KINDS:
                            continue
                        diagnostics.error(
                            f"{noun} '{store.key}': '${{{kind}:{key}}}' in a store's own value is not "
                            "supported — it would never resolve. A value is a literal, an environment "
                            "variable name or a store key; use '${value:kind.name.path}' to reference another "
                            "document's field, or escape a literal as '$${...}'.",
                            location=f"spec.{section}[{position}].value",
                            code="store_value_token_unsupported",
                        )
        return diagnostics

    def validate_secret_store_values(self) -> Diagnostics:
        """Two checks on each secret's own declaration (docs/design/deployment-documentation.md, section 2):

        - A secret that sets `version` gets a warning: no resolver reads it, so the latest version is always used.
        - An `azure-keyvault` secret whose `value` is a URL is an error. The value is the secret's name in the vault
          the `azure-keyvault` integration points at; a full URL is sent to Key Vault as a name and fails. A
          resource that needs the secret's address (App Gateway's `key_vault_secret_id`) takes it as a variable.

        Returns:
            One warning per secret with a `version`, one error per Key Vault secret whose value is a URL.
        """
        diagnostics = Diagnostics()
        self._ensure_validated()
        assert self.model is not None
        for position, secret in enumerate(self.model.spec.secrets or []):
            if secret.version is not None:
                diagnostics.warning(
                    f"Secret '{secret.key}': 'version' has no effect yet; the latest version of the secret is "
                    "always read. Remove it, or keep it as documentation only.",
                    location=f"spec.secrets[{position}].version",
                    code="secret_version_ignored",
                )
            if secret.store == SecretStoreType.AZURE_KEYVAULT and _URL_PATTERN.match(str(secret.value)):
                diagnostics.error(
                    f"Secret '{secret.key}': an 'azure-keyvault' value is the secret's name in the vault the "
                    f"integration points at, not a URL ('{secret.value}'). Use the name only. If a resource needs "
                    "the secret's address instead of its content, declare the address as a variable.",
                    location=f"spec.secrets[{position}].value",
                    code="keyvault_secret_value_is_url",
                )
        return diagnostics

    def validate_allowed_stores(self, security: ConfigurationSecurityModel) -> Diagnostics:
        """Check every variable/secret/feature's `store` against the platform's
        allow-lists (`docs/work/gap_fit_v1.md` gap #7, real usage: config-repo's
        `config/stores.yaml`).

        Each of `security`'s three fields is independently optional — `None`
        means that store kind is unrestricted, matching every other
        allow-list in this codebase (`ConfigurationSpecModel.providers`/
        `.topologies`, both `None`-safe the same way). A field that IS set is
        a closed list: real usage deliberately omits a recognized builtin
        store type (`environment`) from it, so builtins get no automatic
        exemption here — same discipline that field's own real-world
        example was found using.

        Args:
            security: The single `Configuration` document's `spec.security`
                (never `None` itself — callers only invoke this when it's
                set; an absent `Configuration`/absent `spec.security` means
                no restriction, checked by the caller, not repeated here).

        Returns:
            One error per variable/secret/feature whose `store` is not in
            the corresponding allow-list.
        """
        diagnostics = Diagnostics()
        self._ensure_validated()
        assert self.model is not None
        spec = self.model.spec

        if security.allowed_variable_stores is not None:
            for variable in spec.variables or []:
                if variable.store.value not in security.allowed_variable_stores:
                    diagnostics.error(
                        f"Variable '{variable.key}': store '{variable.store.value}' is not in the platform's "
                        f"allowed_variable_stores. Available: {sorted(security.allowed_variable_stores)}",
                        location="spec.variables",
                        code="disallowed_store",
                    )

        if security.allowed_secret_stores is not None:
            for secret in spec.secrets or []:
                if secret.store.value not in security.allowed_secret_stores:
                    diagnostics.error(
                        f"Secret '{secret.key}': store '{secret.store.value}' is not in the platform's "
                        f"allowed_secret_stores. Available: {sorted(security.allowed_secret_stores)}",
                        location="spec.secrets",
                        code="disallowed_store",
                    )

        if security.allowed_feature_stores is not None:
            for feature in spec.features or []:
                if feature.store.value not in security.allowed_feature_stores:
                    diagnostics.error(
                        f"Feature '{feature.key}': store '{feature.store.value}' is not in the platform's "
                        f"allowed_feature_stores. Available: {sorted(security.allowed_feature_stores)}",
                        location="spec.features",
                        code="disallowed_store",
                    )

        return diagnostics


def unresolved_value_tokens(
    model: PlatformBaseModel, declared: dict[str, set[str]], owner_name: str, *, output_claimed: bool = False
) -> Diagnostics:
    """Check every Value token in `model` against an already-computed declared-keys set.

    The free-function form `EnvironmentService.validate_document_tokens` wraps
    for the single-environment case. Exists separately because cross-document
    resolution needs the *merged* version: a Deployment's declared keys are
    the union of its Tenant's environments and its own (ADR: tenant merges in
    before deployment), and no single `EnvironmentModel` holds that union.
    Building a synthetic merged model would be a bigger change than lifting
    the walk itself out.

    Also carries the generalized Phase 1 malformed-syntax check (design
    validated 2026-09-28, docs/design/value-token-resolution.md's "Full
    Solution" section): the four fields with their own `field_validator`
    (`dns`/`module`/`network`/`firewall`) already reject a malformed token at
    schema time and can never reach here with one. Every other string in the
    document — `configuration`/`custom` passthrough dicts included — has no
    such validator, so this is the only place a malformed candidate there
    (e.g. gap #8's real `${IMMICH_DB_PASSWORD}`, missing its `kind:` prefix)
    is ever caught. Reuses the existing generic walk instead of adding a
    second, Pydantic-level mechanism (a `model_validator` couldn't have
    walked `configuration`/`custom` either — it's `dict[str, Any]`, which
    Pydantic never recurses into on its own).

    Args:
        model: Any already-validated strata document.
        declared: Keys already declared, by token kind (`var`/`secret`/`feature`).
        owner_name: What to call the source of `declared` in an error message
            (an environment's name, or a description of several merged).
        output_claimed: True when `model` is a document some workspace
            execution step's own `targets` claims (docs/work/gap_fit_v1.md gap #12,
            `WorkspaceService.claimed_document_names()`) — an `${output:}`
            token is then accepted instead of rejected outright. Still
            cannot validate the referenced step/key actually exists or ever
            produces that output (ADR-0006: Context "does not solve output
            validation" — unchanged by this parameter, only the false
            rejection is fixed). `False` (default) preserves every existing
            caller's behavior unchanged.

    A `${param:}` token is malformed here, as in any run-time check: a deployment that holds one is loaded as a
    template and checked only once resolved, with every `${param:}` substituted.

    Returns:
        One error per malformed or unresolved token, each located at the
        field path where it was written.
    """
    diagnostics = Diagnostics()
    # `meta` (name/annotations/labels/tags) is documentation/presentation
    # only — nothing ever resolves a Value token from it (unlike `spec`,
    # which every real resolver reads), and `meta.name` itself is a
    # `PlatformName` (schema-constrained to `^[a-z][a-z0-9_-]*$`, so it can
    # never even contain `${`). A `${...}`-shaped string there is always a
    # false positive (e.g. an annotation's own description quoting the
    # token syntax as documentation) — excluded so authors never need to
    # `$${...}`-escape prose that was never a real token to begin with.
    dumped = model.model_dump(by_alias=True, mode="json")
    dumped.pop("meta", None)
    for path, text in _iter_strings(dumped):
        for candidate in find_malformed_value_tokens(text):
            kinds = "|".join(VALUE_TOKEN_KINDS)
            diagnostics.error(
                f"Malformed Value token {candidate!r}. Expected '${{{kinds}:KEY}}', e.g. '${{var:region}}'. "
                "If this isn't meant to be a strata Value token (e.g. a third-party tool's own "
                "'${...}' syntax), escape it as '$${...}' instead.",
                location=path,
                code="malformed_value_token",
            )
        for kind, key in extract_value_tokens(text):
            if kind == "deployment":
                continue  # judged per deployment by `unresolved_deployment_tokens()`
            if kind == "artifact":
                continue  # judged per deployment by `unresolved_artifact_tokens()`
            if kind == "value":
                # Existence/path/scalar/target-is-literal checks for this
                # kind are a `DocumentIndex` lookup
                # (`semantic_checks.py`'s `_check_value_references()`,
                # docs/design/cross-document-value-references.md's Phase
                # 5, reusing `resolve_document_value_references()` from
                # Phase 3) — not an Environment-declared-keys question
                # this function could ever answer. Skip entirely, same
                # shape as the `output_claimed` early-exit below, not a
                # `declared[kind]` lookup that would otherwise misreport
                # it as `unsupported_value_token_kind`.
                continue
            if kind == "output" and output_claimed:
                # Accepted — some execution step's `targets` claims this
                # document (gap #12), so a real step can legitimately
                # produce this output at deploy time. Key/step existence is
                # still unvalidatable here (Phase 2 has no visibility into
                # what a provisioner will actually output at runtime).
                continue
            if kind not in declared:
                # `output` (and any future token kind) has no declared-keys set to check
                # against here — an output key isn't declared anywhere, it's produced by a
                # prior deploy step at runtime, which Phase 2 (schema + cross-document
                # validation, no execution) has no visibility into. Flag it explicitly
                # rather than crashing on `declared[kind]` (docs/work/gap_fit_v1.md gap #11) —
                # unclaimed by any step (or not one of the fields `output_claimed` callers
                # ever pass True for), `${output:}` remains rejected here, pending Context
                # (ADR-0006) for the parts of this it still doesn't solve (key validation).
                diagnostics.error(
                    f"'${{{kind}:{key}}}' tokens are not supported in this field yet (pending Context, ADR-0006).",
                    location=path,
                    code="unsupported_value_token_kind",
                )
                continue
            if key not in declared[kind]:
                store = _STORE_BY_TOKEN_KIND[kind]
                known = sorted(declared[kind])
                diagnostics.error(
                    f"'${{{kind}:{key}}}' is not declared in environment "
                    f"'{owner_name}' spec.{store}. Declared: {known}",
                    location=path,
                    code="undeclared_value_token",
                )
    return diagnostics


def unresolved_deployment_tokens(model: PlatformBaseModel, deployment_name: str, tenant: str | None) -> Diagnostics:
    """Check every ``${deployment:KEY}`` token in `model` against the deployment being validated.

    The counterpart of `unresolved_value_tokens()`, which skips this kind: a
    deployment token is not declared in an environment, so it needs no
    environment to be judged. It is judged against the deployment (`name`, and
    `tenant` when it has one) that reaches `model`, the same entries
    `deploy run` puts in its token map (`deployment_token_values()`).

    Args:
        model: Any already-validated strata document reachable from the deployment.
        deployment_name: The deployment's `meta.name`.
        tenant: The deployment's resolved `spec.tenant`, or `None`.

    Returns:
        One error per unknown key (`unknown_deployment_token`) and one per
        `${deployment:tenant}` on a deployment with no tenant
        (`unavailable_deployment_token`), each located at the field path
        where the token was written.
    """
    diagnostics = Diagnostics()
    values = deployment_token_values(deployment_name, tenant)
    dumped = model.model_dump(by_alias=True, mode="json")
    dumped.pop("meta", None)
    for path, text in _iter_strings(dumped):
        for kind, key in extract_value_tokens(text):
            if kind != "deployment":
                continue
            if key not in DEPLOYMENT_TOKEN_KEYS:
                diagnostics.error(
                    f"'${{deployment:{key}}}' is not a deployment token. Available: {sorted(DEPLOYMENT_TOKEN_KEYS)}",
                    location=path,
                    code="unknown_deployment_token",
                )
                continue
            try:
                resolve_value_tokens(f"${{deployment:{key}}}", values)
            except ValueError:
                diagnostics.error(
                    f"'${{deployment:{key}}}' needs a {key}, but deployment '{deployment_name}' has none. Set "
                    f"`spec.{key}` on it (or on a base it extends), or do not use this token in the workspace.",
                    location=path,
                    code="unavailable_deployment_token",
                )
    return diagnostics


def unresolved_artifact_tokens(
    model: PlatformBaseModel, artifact_values: dict[str, str], deployment_name: str
) -> Diagnostics:
    """Check every ``${artifact:NAME.FIELD}`` token in `model` against the deployment being validated.

    The counterpart of `unresolved_deployment_tokens()`: an artifact token is not declared in an environment, so it
    is judged against `artifact_values`, the entries `deploy run` puts in its token map for the same deployment
    (`controllers/artifact_tokens.artifact_token_values()`), so the two cannot disagree.

    Args:
        model: Any already-validated strata document reachable from the deployment.
        artifact_values: `artifact_token_values()` for the deployment, its pins applied.
        deployment_name: The deployment's `meta.name`, for the message.

    Returns:
        One error per token that names no artifact or no field (`unknown_artifact_token`), and one per
        ``image_tag`` of an artifact that has no tag and no pin for this deployment (`unavailable_artifact_token`),
        each located at the field path where the token was written.
    """
    diagnostics = Diagnostics()
    dumped = model.model_dump(by_alias=True, mode="json")
    dumped.pop("meta", None)
    for path, text in _iter_strings(dumped):
        for kind, key in extract_value_tokens(text):
            if kind != "artifact":
                continue
            problem = artifact_token_problem(key, artifact_values)
            if problem is None:
                continue
            name, field = split_artifact_token_key(key)
            if field in ARTIFACT_TOKEN_FIELDS and artifact_key(name, "image_name") in artifact_values:
                diagnostics.error(
                    f"{problem} (deployment '{deployment_name}')",
                    location=path,
                    code="unavailable_artifact_token",
                )
            else:
                diagnostics.error(problem, location=path, code="unknown_artifact_token")
    return diagnostics


def _iter_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Yield every `(dotted_path, string)` pair inside a nested structure.

    Paths are built from the document's own keys, so they read as the field
    path an author would recognise (`spec.zones[0].records[1].value`).
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path or "<root>", value))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_iter_strings(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for position, child in enumerate(value):
            found.extend(_iter_strings(child, f"{path}[{position}]"))
    return found


class EnvironmentTemplateService(BaseService[EnvironmentTemplateModel]):
    """Loads an environment whose spec holds `${param:}`: meta validated, spec kept as written.

    The loader picks it over `EnvironmentService` for such a document (`solution_controller.py`); resolution
    validates the spec per environment that resolves through it, once the values are substituted.
    """

    def _get_model_class(self) -> type[EnvironmentTemplateModel]:
        """Return the EnvironmentTemplateModel class for validation."""
        return EnvironmentTemplateModel


def merge_environment_models(
    environments: list[EnvironmentModel],
) -> tuple[dict[str, VariableStoreModel], dict[str, SecretStoreModel], dict[str, FeatureStoreModel]]:
    """Merge several Environments into one set of store definitions, by key.

    Later entries in `environments` override earlier ones on a key collision
    — the actual store definition (not just its presence), since a real
    resolution needs to know *which* store backs a key, not only that one
    does. Callers merge a Tenant's `spec.environments` before a Deployment's
    own (`TenantSpecModel.environments`'s own description: "merged in BEFORE
    a deployment's own"), so pass them in that order.

    Args:
        environments: Already-validated Environment models, in merge order.

    Returns:
        Three dicts (variables, secrets, features), each keyed by `key`.
    """
    variables: dict[str, VariableStoreModel] = {}
    secrets: dict[str, SecretStoreModel] = {}
    features: dict[str, FeatureStoreModel] = {}
    for environment in environments:
        spec = environment.spec
        variables.update({v.key: v for v in spec.variables or []})
        secrets.update({s.key: s for s in spec.secrets or []})
        features.update({f.key: f for f in spec.features or []})
    return variables, secrets, features


def merge_workspace_environment_deployment_properties(
    workspace: WorkspaceModel,
    environments: list[EnvironmentModel],
    deployment: DeploymentModel,
    source: str,
) -> dict[str, Any]:
    """Deep-merge `{source}` (`"properties"` or `"custom"`):
    `workspace.spec.{source}` -> each reachable `environment.spec.{source}`
    in order -> `deployment.spec.{source}` (docs/design/
    build-time-value-categories.md, Q3 - a deliberate improvement over v1's
    real, docstring-contradicting behaviour, which never merges the
    deployment's own value at all).

    No `overrides.{source}` step, unlike v1: `EnvironmentSpecModel` has no
    `overrides` field in v2 at all - confirmed via its own module docstring,
    deliberately not ported (0 of 26 real environment documents used it,
    ADR-0003's minimal-slice policy).

    Moved here from `value_controller.py` (docs/archive/
    environment-value-metadata.md Phase 3) — pure, no `SolutionContext`
    dependency, so it belongs in `services`, not `controllers`: callers
    needing the merged view at validate time (`semantic_checks.py`) would
    otherwise recreate the cycle `value_references.py` documents
    (`solution_context` -> `semantic_checks` -> `value_controller` ->
    `solution_context`).
    """
    result: dict[str, Any] = dict(getattr(workspace.spec, source, None) or {})
    for environment in environments:
        result = deep_merge(result, getattr(environment.spec, source, None) or {})
    result = deep_merge(result, getattr(deployment.spec, source, None) or {})
    return result
