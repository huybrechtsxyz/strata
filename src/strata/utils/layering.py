"""Shared utilities for scoped layering scheme resolution.

Provides ``resolve_layering_scheme()`` which maps a deployment file path to the first
matching ``ScopedLayeringModel`` entry from ``configuration.spec.layerings``, and
``compute_artifact_path()`` which builds the slash-separated artifact path from a
deployment's layer values and the resolved scheme.

Both the deployment service and the overlap controller use these helpers so the
resolution logic is never duplicated.
"""

from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from strata.models.configuration_model import ScopedLayeringModel


def resolve_layering_scheme(
    deployment_file_path: str,
    work_path: str,
    layerings: "List[ScopedLayeringModel]",
) -> "Optional[ScopedLayeringModel]":
    """Return the first scheme whose scope matches the deployment file's relative path.

    First-match wins — order in ``spec.layerings`` determines precedence.  Returns
    ``None`` when no scheme matches, meaning no layering validation is applied for
    that deployment file.

    Args:
        deployment_file_path: Absolute or relative path to the deployment file.
        work_path: Workspace root used to compute the relative path.
        layerings: Ordered list of scoped schemes from ``configuration.spec.layerings``.

    Returns:
        The first matching ``ScopedLayeringModel``, or ``None`` if none match.
    """
    if not layerings:
        return None

    try:
        rel = Path(deployment_file_path).relative_to(work_path).as_posix()
    except (ValueError, TypeError):
        # Fallback: use the filename only when the path is not under work_path.
        rel = Path(deployment_file_path).name

    for scheme in layerings:
        if fnmatch(rel, scheme.scope):
            return scheme

    return None


def compute_artifact_path(
    deployment_values: Dict[str, str],
    scheme: "ScopedLayeringModel",
) -> str:
    """Build the artifact path from deployment layer values using a resolved scheme.

    Iterates ``scheme.layers`` in order, appending each layer's value (or its
    configured default when the key is absent).  Layers with neither a value nor a
    default are skipped.

    Args:
        deployment_values: The ``deployment.spec.layers`` dict ``{layer_name: value}``.
        scheme: The resolved ``ScopedLayeringModel`` to apply.

    Returns:
        Slash-separated path string (e.g. ``"europe/contoso/dev"``), or ``""`` when
        no layer values can be resolved.
    """
    components: List[str] = []
    for layer in scheme.layers:
        value: Optional[str] = deployment_values.get(layer.name)
        if value is None and layer.default:
            value = layer.default
        if value is not None:
            components.append(str(value))
    return "/".join(components)
