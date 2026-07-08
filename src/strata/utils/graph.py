"""Pure dataclasses and rendering functions for workspace dependency graphs."""

from __future__ import annotations

import base64
import zlib
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


def slugify_path(path: str) -> str:
    """Convert a file path into a valid Mermaid node ID.

    Algorithm: strip extension → replace /, -, . with _ → lowercase.
    """
    # Strip extension
    if "." in path.split("/")[-1]:
        path = path.rsplit(".", 1)[0]
    return path.replace("/", "_").replace("\\", "_").replace("-", "_").replace(".", "_").lower()


# CSS class mapping for node status
_STATUS_CLASSES = {
    "valid": "valid",
    "invalid": "invalid",
    "missing": "missing",
    "external": "external",
    "orphan": "orphan",
    "active": "valid",
    "disabled": "disabled",
    "dangling": "missing",
}


def render_mermaid(result: GraphResult, direction: str = "LR") -> str:
    """Render a file-mode GraphResult as a Mermaid graph definition."""
    lines: list[str] = []
    lines.append(f"graph {direction}")
    lines.append("  classDef valid fill:#d4edda,stroke:#28a745")
    lines.append("  classDef invalid fill:#fff3cd,stroke:#ffc107")
    lines.append("  classDef missing fill:#f8d7da,stroke:#dc3545")
    lines.append("  classDef external fill:#e2e3e5,stroke:#6c757d")
    lines.append("  classDef orphan fill:#f5f5f5,stroke:#adb5bd,stroke-dasharray:5")
    lines.append("")

    # Nodes
    for node in result.nodes:
        node_id = slugify_path(node.identifier)
        if node.name and node.path:
            label = f"{node.name} ({node.path})"
        elif node.name:
            label = node.name
        else:
            label = node.identifier
        css_class = _STATUS_CLASSES.get(node.status, "valid")
        lines.append(f'  {node_id}["{label}"]:::{css_class}')

    lines.append("")

    # Edges
    for edge in result.edges:
        src_id = slugify_path(edge.source)
        tgt_id = slugify_path(edge.target)
        if edge.label:
            lines.append(f"  {src_id} -->|{edge.label}| {tgt_id}")
        else:
            lines.append(f"  {src_id} --> {tgt_id}")

    return "\n".join(lines)


def render_mermaid_resources(result: GraphResult, direction: str = "TD") -> str:
    """Render a resource-mode GraphResult with topology subgraphs."""
    lines: list[str] = []
    lines.append(f"graph {direction}")
    lines.append("  classDef resource fill:#dbeafe,stroke:#2563eb")
    lines.append("  classDef module fill:#fef3c7,stroke:#d97706")
    lines.append("  classDef namespace fill:#d1fae5,stroke:#059669")
    lines.append("  classDef network fill:#e0e7ff,stroke:#4f46e5")
    lines.append("  classDef disabled fill:#e2e3e5,stroke:#6c757d")
    lines.append("  classDef missing fill:#f8d7da,stroke:#dc3545")
    lines.append("")

    # Topology subgraphs
    assigned_nodes: set[str] = set()
    for topo in result.topologies:
        subgraph_id = slugify_path(topo.name)
        label = f"{topo.name} ({topo.provisioner})"
        if topo.type:
            label += f" · {topo.type}"
        lines.append(f'  subgraph {subgraph_id}["{label}"]')
        for comp_name in topo.components:
            node = _find_node(result, comp_name)
            if node:
                css = _resource_css_class(node)
                lines.append(f'    {slugify_path(comp_name)}["{node.name or comp_name}"]:::{css}')
                assigned_nodes.add(comp_name)
        for ns_name in topo.namespaces:
            node = _find_node(result, ns_name)
            if node:
                lines.append(f'    {slugify_path(ns_name)}{{{{"{node.name or ns_name}"}}}}:::namespace')
                assigned_nodes.add(ns_name)
        lines.append("  end")
        lines.append("")

    # Unassigned nodes
    unassigned = [n for n in result.nodes if n.identifier not in assigned_nodes]
    if unassigned:
        lines.append('  subgraph unassigned["(unassigned)"]')
        for node in unassigned:
            css = _resource_css_class(node)
            lines.append(f'    {slugify_path(node.identifier)}["{node.name or node.identifier}"]:::{css}')
        lines.append("  end")
        lines.append("")

    # Edges
    for edge in result.edges:
        src_id = slugify_path(edge.source)
        tgt_id = slugify_path(edge.target)
        if edge.label.startswith("subnet"):
            lines.append(f"  {src_id} -.->|{edge.label}| {tgt_id}")
        elif edge.label:
            lines.append(f"  {src_id} -->|{edge.label}| {tgt_id}")
        else:
            lines.append(f"  {src_id} --> {tgt_id}")

    return "\n".join(lines)


def render_tree(result: GraphResult) -> str:
    """Render GraphResult as a text tree for console output."""
    if result.mode == "resources":
        return _render_resource_tree(result)
    return _render_file_tree(result)


def render_mermaid_live_url(mermaid_source: str) -> str:
    """Generate a Mermaid Live Editor URL from source."""
    compressed = zlib.compress(mermaid_source.encode("utf-8"), level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii")
    return f"https://mermaid.live/edit#pako:{encoded}"


def compute_deployment_order(result: GraphResult) -> list[list[str]]:
    """Topological sort of resources by depends_on edges. Returns layers."""
    # Build adjacency map from depends_on edges
    deps: dict[str, set[str]] = {}
    all_nodes: set[str] = set()
    for gnode in result.nodes:
        if gnode.kind in ("resource", "network"):
            all_nodes.add(gnode.identifier)
            deps.setdefault(gnode.identifier, set())
    for edge in result.edges:
        if edge.label == "depends_on":
            deps.setdefault(edge.source, set()).add(edge.target)
            all_nodes.add(edge.source)
            all_nodes.add(edge.target)

    # Kahn's algorithm — compute in-degree (count how many things each node depends on)
    in_degree: dict[str, int] = {n: 0 for n in all_nodes}
    for src, src_deps in deps.items():
        for dep in src_deps:
            if dep in in_degree:
                in_degree[src] += 1

    layers: list[list[str]] = []
    remaining = set(all_nodes)
    while remaining:
        layer = [n for n in remaining if in_degree.get(n, 0) == 0]
        if not layer:
            # Cycle detected — break out with remaining nodes
            layers.append(sorted(remaining))
            break
        layers.append(sorted(layer))
        for n in layer:
            remaining.discard(n)
            # Reduce in-degree of dependents
            for other in remaining:
                if n in deps.get(other, set()):
                    in_degree[other] -= 1

    return layers


# ─── Private helpers ──────────────────────────────────────────────────────────


def _find_node(result: GraphResult, identifier: str) -> Optional[GraphNode]:
    for node in result.nodes:
        if node.identifier == identifier:
            return node
    return None


def _resource_css_class(node: GraphNode) -> str:
    kind_map = {
        "resource": "resource",
        "module": "module",
        "namespace": "namespace",
        "network": "network",
    }
    if node.status == "disabled":
        return "disabled"
    if node.status in ("missing", "dangling"):
        return "missing"
    return kind_map.get(node.kind, "resource")


_STATUS_ICONS = {
    "valid": "✅",
    "invalid": "⚠️",
    "missing": "❌",
    "external": "🔗",
    "orphan": "◌",
    "active": "✅",
    "disabled": "⊘",
    "dangling": "❌",
}


def _render_file_tree(result: GraphResult) -> str:
    """Render file graph as indented tree."""
    lines: list[str] = []

    # Build adjacency for tree traversal
    children: dict[str, list[tuple[str, str]]] = {}
    has_parent: set[str] = set()
    for edge in result.edges:
        children.setdefault(edge.source, []).append((edge.target, edge.label))
        has_parent.add(edge.target)

    # Root nodes are entry points or nodes with no parent
    roots = result.entry_points or [n.identifier for n in result.nodes if n.identifier not in has_parent]

    def _format_node(node: Optional[GraphNode], identifier: str) -> str:
        icon = _STATUS_ICONS.get(node.status if node else "missing", "")
        if node and node.name and node.path:
            return f"{node.name} ({node.path}) [{node.kind}] {icon}"
        elif node and node.name:
            return f"{node.name} [{node.kind}] {icon}"
        return f"{identifier} {icon}"

    def _walk(identifier: str, prefix: str, is_last: bool) -> None:
        node = _find_node(result, identifier)
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{_format_node(node, identifier)}")
        child_prefix = prefix + ("    " if is_last else "│   ")
        kids = children.get(identifier, [])
        for i, (child_id, _label) in enumerate(kids):
            _walk(child_id, child_prefix, i == len(kids) - 1)

    for i, root_id in enumerate(roots):
        node = _find_node(result, root_id)
        lines.append(_format_node(node, root_id))
        kids = children.get(root_id, [])
        for j, (child_id, _label) in enumerate(kids):
            _walk(child_id, "", j == len(kids) - 1)
        if i < len(roots) - 1:
            lines.append("")

    # Summary
    status_counts: dict[str, int] = {}
    for node in result.nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1
    summary_parts = [f"{count} {status}" for status, count in sorted(status_counts.items())]
    lines.append("")
    lines.append(f"Summary: {', '.join(summary_parts)}")

    return "\n".join(lines)


def _render_resource_tree(result: GraphResult) -> str:
    """Render resource graph as topology tree."""
    lines: list[str] = []

    for topo in result.topologies:
        lines.append(f"{topo.name} ({topo.provisioner} · {topo.type})")
        all_items = list(topo.components) + list(topo.namespaces)
        for i, item in enumerate(all_items):
            node = _find_node(result, item)
            is_last = i == len(all_items) - 1
            connector = "└── " if is_last else "├── "
            kind_label = f" ({node.kind})" if node else ""
            name = node.name if node else item
            lines.append(f"{connector}{name}{kind_label}")
            # Show edges from this node
            node_edges = [e for e in result.edges if e.source == item]
            for j, edge in enumerate(node_edges):
                edge_last = j == len(node_edges) - 1
                child_prefix = "    " if is_last else "│   "
                edge_connector = "└── " if edge_last else "├── "
                lines.append(f"{child_prefix}{edge_connector}→ {edge.label}: {edge.target}")
        lines.append("")

    # Summary
    resources = sum(1 for n in result.nodes if n.kind == "resource")
    modules = sum(1 for n in result.nodes if n.kind == "module")
    namespaces = sum(1 for n in result.nodes if n.kind == "namespace")
    networks = sum(1 for n in result.nodes if n.kind == "network")
    lines.append(f"Resources: {resources} | Modules: {modules} | Namespaces: {namespaces} | Networks: {networks}")

    return "\n".join(lines)
