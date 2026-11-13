#!/usr/bin/env python3
"""Service for loading and validating artifact configuration."""

from strata.models.artifact_model import ArtifactModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.sbom_utils import pull_registry, registry_host, registry_prefix, split_registry


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
        (docs/archive/artifact-gap-analysis.md, design 5).

        `registry` is documentation: strata never puts it in the image reference. A `registry` that names another
        host than `image_name` is pulled from is information (`artifact_registry_mismatch`), not a warning: a
        runtime that pulls `image_name` as written (a Compose service) pulls it from elsewhere than the document
        says, but a consumer that adds its own registry (a Terraform root, a chart's `image.registry`) is right to
        get a host-less path, and `validate --strict` must not fail on it. Nothing to check when `registry` is unset.
        """
        diagnostics = Diagnostics()
        self._ensure_validated()
        assert self.model is not None
        spec = self.model.spec
        if not spec.registry:
            return diagnostics
        declared = registry_host(spec.registry)
        prefix = registry_prefix(spec.registry)
        pulled = pull_registry(spec.image_name)
        if declared is None or prefix is None or declared == pulled:
            return diagnostics
        host, repository = split_registry(spec.image_name)
        suggested = _join_registry(prefix, repository)
        diagnostics.info(
            f"Artifact '{self.model.meta.name}': spec.registry is '{spec.registry}', but image_name "
            f"'{spec.image_name}' names "
            + ("no registry host (Docker Hub, when pulled as written)" if host is None else f"'{pulled}'")
            + ". strata does not combine the two. Where image_name is pulled as written (a Compose service), write "
            f"the full reference, for example image_name: {suggested}. Where a consumer adds its own registry (a "
            "Terraform root, a chart's image.registry), nothing is wrong.",
            location="spec.registry",
            code="artifact_registry_mismatch",
        )
        return diagnostics


def _join_registry(prefix: str, repository: str) -> str:
    """`prefix/repository`, without repeating a path `repository` already starts with (`acr.io/team` and
    `team/api` give `acr.io/team/api`)."""
    _, _, path = prefix.partition("/")
    if path and (repository == path or repository.startswith(path + "/")):
        return f"{prefix.partition('/')[0]}/{repository}"
    return f"{prefix}/{repository}"
