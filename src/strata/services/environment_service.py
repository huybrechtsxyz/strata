#!/usr/bin/env python3
"""Service for loading and validating environment configuration."""

from typing import Any

from strata.models.common_models import PlatformBaseModel
from strata.models.configuration_model import ConfigurationSecurityModel
from strata.models.environment_model import EnvironmentModel
from strata.models.store_model import FeatureStoreModel, SecretStoreModel, VariableStoreModel, VariableStoreType
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.value_tokens import VALUE_TOKEN_KINDS, extract_value_tokens, find_malformed_value_tokens

#: Maps a Value token's kind to the environment store that declares it.
_STORE_BY_TOKEN_KIND = {"var": "variables", "secret": "secrets", "feature": "features"}


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
        `ArtifactModel` (docs/design/artifact-references.md).

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

    def validate_allowed_stores(self, security: ConfigurationSecurityModel) -> Diagnostics:
        """Check every variable/secret/feature's `store` against the platform's
        allow-lists (`docs/design/gap_fit_v1.md` gap #7, real usage: cfg-int-deployment's
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
            execution step's own `targets` claims (docs/design/gap_fit_v1.md gap #12,
            `WorkspaceService.claimed_document_names()`) — an `${output:}`
            token is then accepted instead of rejected outright. Still
            cannot validate the referenced step/key actually exists or ever
            produces that output (ADR-0006: Context "does not solve output
            validation" — unchanged by this parameter, only the false
            rejection is fixed). `False` (default) preserves every existing
            caller's behavior unchanged.

    Returns:
        One error per malformed or unresolved token, each located at the
        field path where it was written.
    """
    diagnostics = Diagnostics()
    for path, text in _iter_strings(model.model_dump(by_alias=True, mode="json")):
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
                # rather than crashing on `declared[kind]` (docs/design/gap_fit_v1.md gap #11) —
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
