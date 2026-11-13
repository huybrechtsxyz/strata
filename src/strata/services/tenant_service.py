#!/usr/bin/env python3
"""Service for loading and validating tenant configuration."""

from typing import Any

from strata.models.provider_config_model import ProviderConfigModel
from strata.models.tenant_model import TenantModel, TenantTemplateModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.dict_merge import deep_merge


def merge_tenant_specs(base: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge a base tenant's spec into a child's, on dumped dicts (`base-parameters-v2.md` (work doc)).

    - `display_name`, `onboarded`: the child's when it sets one.
    - `geographies`: **replaced** by the child's list when it sets one, never joined: it is a data-residency limit,
      and a child that writes `[us]` must not also allow its base's `europe`.
    - `environments`: the base's list, then the child's entries not already in it, as on a deployment.
    - `properties`, `custom`, `configuration`: deep-merged per leaf key, child wins.
    - `partial`, `extends`: consumed and stripped from the result.

    Neither input is mutated.
    """
    merged: dict[str, Any] = deep_merge(base, child)
    base_envs = base.get("environments") or []
    child_envs = child.get("environments") or []
    if base_envs or child_envs:
        merged["environments"] = [*base_envs, *[name for name in child_envs if name not in base_envs]]
    merged.pop("partial", None)
    merged.pop("extends", None)
    return merged


class TenantService(BaseService[TenantModel]):
    """Service for handling tenant configuration.

    `spec.environments` existence is already checked — it is typed
    `Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)]`
    (`tenant_model.py`), which `references.py`'s generic reference-existence
    walker checks for every document unconditionally, no per-kind opt-in
    needed. No dedicated method lives here for it (unlike
    `validate_geographies_against_provider_configs()` below, which needs a
    second document's *content*, not just its existence).
    """

    def _get_model_class(self) -> type[TenantModel]:
        """Return the TenantModel class for validation."""
        return TenantModel

    def validate_geographies_against_provider_configs(
        self, provider_configs: dict[str, ProviderConfigModel]
    ) -> Diagnostics:
        """Check every declared geography is one some provider region actually has.

        v1 validated a tenant's `zones` against a `configuration.spec.zones`
        registry that v2 has not ported (ADR-0003). It turns out not to be
        needed: `ProviderConfigRegionModel.geography` already declares, per
        region, which compliance/deployment boundary it belongs to — the same
        concept under the name the provider side settled on. The set of valid
        geographies is therefore derivable from the provider registries that
        exist today, which is why this check is implemented rather than parked
        with the others.

        This is the existence half. The stronger check — "the region this
        tenant's deployment actually targets is inside an allowed geography" —
        needs a `Deployment` binding a tenant to a workspace, which is not
        built yet.

        Args:
            provider_configs: Already-loaded `ProviderConfigModel` documents,
                keyed by provider type name — the caller resolves them from
                the solution index.

        Returns:
            One error per geography no provider region declares.
        """
        diagnostics = Diagnostics()
        if self.model is None:
            diagnostics.error("Tenant model is not initialized")
            return diagnostics

        known: set[str] = set()
        for config in provider_configs.values():
            for region in config.spec.regions or []:
                if region.geography:
                    known.add(region.geography)

        if not known:
            return diagnostics  # no provider declares any geography — nothing to check against

        for index, geography in enumerate(self.model.spec.geographies or []):
            if geography not in known:
                diagnostics.error(
                    f"Tenant '{self.model.meta.name}': geography '{geography}' is not declared by any "
                    f"provider region. Known geographies: {sorted(known)}",
                    location=f"spec.geographies.{index}",
                    code="unknown_geography",
                )
        return diagnostics


class TenantTemplateService(BaseService[TenantTemplateModel]):
    """Loads a tenant whose spec holds `${param:}`: meta validated, spec kept as written.

    The loader picks it over `TenantService` for such a document (`solution_controller.py`); resolution validates
    the spec per tenant that resolves through it, once the values are substituted.
    """

    def _get_model_class(self) -> type[TenantTemplateModel]:
        """Return the TenantTemplateModel class for validation."""
        return TenantTemplateModel
