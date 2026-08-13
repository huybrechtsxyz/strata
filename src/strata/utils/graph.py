"""Pure dataclasses for workspace dependency graphs.

Rendering used to live here as hand-written Mermaid string concatenation. It now
lives in shipped ``kind: diagram`` YAML definitions (ADR-0034), which reach this
data through ``DiagramSourceController``. This module is the data shape only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GraphNode:
    """A node in the workspace dependency graph."""

    identifier: str
    path: Optional[str] = None
    name: Optional[str] = None
    kind: str = ""
    status: str = "valid"
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """A directed edge between two graph nodes."""

    source: str
    target: str
    label: str = ""


@dataclass
class GraphTopology:
    """Topology subgraph metadata (resource mode only)."""

    name: str
    provisioner: str = ""
    provider: str = ""
    type: str = ""
    components: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)


@dataclass
class GraphResult:
    """Complete graph result with nodes, edges, and metadata."""

    mode: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    topologies: list[GraphTopology] = field(default_factory=list)
    # meta.name of the workspace a resource-mode graph was built from. Needed to
    # form structural strata:// URIs (strata://workspace/<name>/resource/<id>).
    workspace_name: str = ""


_FILE_EXTENSIONS = ("yaml", "yml")


def slugify_path(path: str) -> str:
    """Convert a file path — or any other identifier — into a valid Mermaid node ID.

    Algorithm: strip a trailing ``.yaml``/``.yml`` extension -> rewrite a leading
    cross-repo ``@`` marker -> replace ``/``, ``-`` and ``.`` with ``_`` -> lowercase.

    Only a recognised strata file extension is stripped — not every dot marks a
    file extension. A DNS zone name like ``example.com`` has no file behind it;
    treating the ``.com`` as an "extension" would truncate it to ``example`` and
    collide with ``example.org``. Real strata documents always end in ``.yaml``
    or ``.yml`` (see ``GraphController._iter_yaml_files``), so this is exhaustive
    for actual file paths while leaving other dotted identifiers intact.

    The ``@`` needs rewriting rather than replacing: Mermaid rejects an ``@`` in
    a node ID outright, and mapping it to ``_`` would leave a leading underscore.
    ``@repo/path`` therefore becomes ``at_repo_path``.
    """
    last_segment = path.split("/")[-1]
    if "." in last_segment and last_segment.rsplit(".", 1)[-1].lower() in _FILE_EXTENSIONS:
        path = path.rsplit(".", 1)[0]
    if path.startswith("@"):
        path = "at_" + path[1:]
    return path.replace("/", "_").replace("\\", "_").replace("-", "_").replace(".", "_").lower()
