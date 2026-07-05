"""Provenance metadata captured when multiple environment files are merged."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MergeProvenance:
    """Tracks which environment file contributed each key during ``merge_envfiles()``.

    Populated by :meth:`~strata.services.environment_service.EnvironmentService.merge_envfiles`
    and propagated through :class:`~strata.services.deployment_service.DeploymentService` into
    :class:`~strata.utils.resolved_values.ResolvedValues`.

    For single-file deployments all source dicts will map every key to that single file path,
    and ``merge_order`` will contain exactly one entry.

    Attributes:
        merge_order:         Ordered list of env file paths (relative to work_path) as they were
                             processed — earliest first.
        variable_sources:    ``{key: file_path}`` — which file last set this variable key.
        secret_sources:      ``{key: file_path}`` — which file last set this secret key.
        feature_sources:     ``{key: file_path}`` — which file last set this feature key.
        variable_overridden: ``{key: [file_path, ...]}`` — earlier files that declared
                             this variable before it was overridden (does NOT include the
                             final winner file).
        secret_overridden:   Same as ``variable_overridden`` but for secrets.
        feature_overridden:  Same as ``variable_overridden`` but for features.
    """

    merge_order: List[str] = field(default_factory=list)
    variable_sources: Dict[str, str] = field(default_factory=dict)
    secret_sources: Dict[str, str] = field(default_factory=dict)
    feature_sources: Dict[str, str] = field(default_factory=dict)
    variable_overridden: Dict[str, List[str]] = field(default_factory=dict)
    secret_overridden: Dict[str, List[str]] = field(default_factory=dict)
    feature_overridden: Dict[str, List[str]] = field(default_factory=dict)

    def is_multi_file(self) -> bool:
        """Return True when more than one file was merged."""
        return len(self.merge_order) > 1
