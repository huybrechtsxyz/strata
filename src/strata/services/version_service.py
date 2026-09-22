#!/usr/bin/env python3
"""Service for loading and validating version configuration."""

from strata.models.version_model import VersionModel, VersionPinModel
from strata.services.base_service import BaseService


class VersionService(BaseService[VersionModel]):
    """Service for handling version configuration.

    `resolve()` is the single lookup the overlay uses: when a document
    declares a version and the selected version document pins that same
    target, the pin wins. Keeping it here (rather than inlining dict access at
    each of the four target sites) means one place emits the resolution and
    one place can log it.

    The overlay itself is not wired yet — it belongs with the controller's
    resolution pass, alongside `extends` merging and the other cross-document
    checks. Until then this validates and indexes like any other kind.
    """

    def _get_model_class(self) -> type[VersionModel]:
        """Return the VersionModel class for validation."""
        return VersionModel

    def resolve(self, category: str, name: str) -> VersionPinModel | None:
        """Return the pin for `name` in `category`, or None when unpinned.

        Args:
            category: One of `PIN_CATEGORIES` ('images', 'charts', 'remotes', 'tools').
            name: The target's own name.

        Returns:
            The matching `VersionPinModel`, or None when this document does
            not pin that target — meaning the declaring document's own value
            stands.
        """
        if self.model is None:
            return None
        entries: dict[str, VersionPinModel] | None = getattr(self.model.spec.pins, category, None)
        return (entries or {}).get(name)
