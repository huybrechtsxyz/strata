#!/usr/bin/env python3
"""Service for loading and validating artifact configuration."""

from strata.models.artifact_model import ArtifactModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.sbom_utils import pull_registry, registry_host, split_registry


class ArtifactService(BaseService[ArtifactModel]):
    """Service for handling artifact configuration.

    `spec.integration` referencing a real Integration with the `sources` capability is still not cross-checked:
    deferred until real usage exists (docs/archive/artifact-references.md), same discipline as
    `TenantService.spec.environments`.
    """

    def _get_model_class(self) -> type[ArtifactModel]:
        """Return the ArtifactModel class for validation."""
        return ArtifactModel

    def validate_registry(self) -> Diagnostics:
        """`spec.registry` agrees with the registry `spec.image_name` is pulled from
        (docs/work/artifact-gap-analysis.md, design 5).

        `registry` is documentation: strata never puts it in the image reference, so the runtime pulls
        `image_name` as written, from its own host part or Docker Hub. A `registry` that names another host is a
        warning (`artifact_registry_mismatch`), since the reference strata delivers points elsewhere than the
        document says. Nothing to check when `registry` is unset.
        """
        diagnostics = Diagnostics()
        self._ensure_validated()
        assert self.model is not None
        spec = self.model.spec
        if not spec.registry:
            return diagnostics
        declared = registry_host(spec.registry)
        pulled = pull_registry(spec.image_name)
        if declared is None or declared == pulled:
            return diagnostics
        _, repository = split_registry(spec.image_name)
        diagnostics.warning(
            f"Artifact '{self.model.meta.name}': spec.registry is '{spec.registry}', but image_name "
            f"'{spec.image_name}' is pulled from '{pulled}'"
            + (" (Docker Hub: the name has no registry host)" if split_registry(spec.image_name)[0] is None else "")
            + ". strata does not combine the two: the runtime pulls image_name as written. Write the full path, "
            f"for example image_name: {declared}/{repository}.",
            location="spec.registry",
            code="artifact_registry_mismatch",
        )
        return diagnostics
