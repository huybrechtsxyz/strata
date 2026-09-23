#!/usr/bin/env python3
"""Service for loading and validating environment configuration."""

from typing import Any

from strata.models.common_models import PlatformBaseModel
from strata.models.environment_model import EnvironmentModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.value_tokens import extract_value_tokens

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
        declared = self.declared_keys()
        diagnostics = Diagnostics()

        for path, text in _iter_strings(model.model_dump(by_alias=True, mode="json")):
            for kind, key in extract_value_tokens(text):
                if key not in declared[kind]:
                    store = _STORE_BY_TOKEN_KIND[kind]
                    known = sorted(declared[kind])
                    diagnostics.error(
                        f"'${{{kind}:{key}}}' is not declared in environment "
                        f"'{self.model.meta.name}' spec.{store}. Declared: {known}",  # type: ignore[union-attr]
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
