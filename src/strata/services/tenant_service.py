#!/usr/bin/env python3
"""Service for loading and validating tenant configuration."""

from strata.models.provider_config_model import ProviderConfigModel
from strata.models.tenant_model import TenantModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics


class TenantService(BaseService[TenantModel]):
    """Service for handling tenant configuration.

    `spec.environments` cross-checking is still deferred: it names Environment
    documents and the `environment` kind is not built yet (it is the most
    authored kind missing from v2). Becomes an index lookup once it lands.
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

        for index, geography in enumerate(self.model.spec.geographies):
            if geography not in known:
                diagnostics.error(
                    f"Tenant '{self.model.meta.name}': geography '{geography}' is not declared by any "
                    f"provider region. Known geographies: {sorted(known)}",
                    location=f"spec.geographies.{index}",
                    code="unknown_geography",
                )
        return diagnostics
