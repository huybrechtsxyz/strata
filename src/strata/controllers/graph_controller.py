"""Controller for building workspace dependency graphs."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from strata.controllers.base_controller import BaseController
from strata.utils.graph import GraphEdge, GraphNode, GraphResult, GraphTopology


class GraphController(BaseController):
    """Build a workspace dependency graph from YAML file references."""

    def __init__(self, work_path: Path, entry: Optional[str] = None, no_validate: bool = False) -> None:
        super().__init__()
        self._work_path = work_path
        self._entry = entry
        self._no_validate = no_validate
        self._visited: set[str] = set()

    def build_file_graph(self) -> GraphResult:
        """Build the file dependency graph."""
        result = GraphResult(mode="files")

        # Discover entry points
        entry_paths = self._discover_entry_points()
        if not entry_paths:
            self._errors.append("No deployment entry points found in workspace.")
            return result

        result.entry_points = [str(p.relative_to(self._work_path)) for p in entry_paths]

        # Recursively walk file references from each entry point
        for entry_path in entry_paths:
            self._walk_file(entry_path, result)

        # Find orphan files (exist in workspace but not referenced)
        if not self._entry:
            self._find_orphan_files(result)

        return result

    def build_resource_graph(self) -> GraphResult:
        """Build the logical resource topology graph."""
        result = GraphResult(mode="resources")

        # Find the workspace file
        ws_path = self._resolve_workspace_path()
        if not ws_path:
            self._errors.append("Cannot resolve workspace file. Use --entry to specify a deployment or workspace file.")
            return result

        # Parse workspace
        ws_data = self._parse_yaml(ws_path)
        if not ws_data:
            self._errors.append(f"Failed to parse workspace file: {ws_path}")
            return result

        spec = ws_data.get("spec", {})
        ws_name = ws_data.get("meta", {}).get("name", ws_path.stem)
        result.entry_points = [str(ws_path.relative_to(self._work_path))]

        # Extract resources
        resources = spec.get("resources") or []
        for res in resources:
            node = GraphNode(
                identifier=res["name"],
                name=res["name"],
                kind="resource",
                status="active" if res.get("enabled", True) else "disabled",
                metadata={
                    "role": res.get("role"),
                    "subnet": res.get("subnet"),
                    "file": res.get("file"),
                },
            )
            result.nodes.append(node)

            # depends_on edges
            for dep in res.get("depends_on") or []:
                result.edges.append(GraphEdge(source=res["name"], target=dep, label="depends_on"))

            # module edges
            for mod in res.get("modules") or []:
                mod_name = mod["name"]
                mod_node = GraphNode(
                    identifier=mod_name,
                    name=mod_name,
                    kind="module",
                    status="active" if mod.get("enabled", True) else "disabled",
                    metadata={"slot_type": mod.get("slot_type", "main"), "parent_resource": res["name"]},
                )
                result.nodes.append(mod_node)
                result.edges.append(GraphEdge(source=res["name"], target=mod_name, label="runs"))

            # subnet edge
            subnet = res.get("subnet")
            if subnet and "/" in subnet:
                net_name = subnet.split("/")[0]
                result.edges.append(GraphEdge(source=res["name"], target=net_name, label=f"subnet: {subnet}"))

            # firewall edges
            for fw in res.get("firewalls") or []:
                result.edges.append(GraphEdge(source=res["name"], target=fw, label="firewall"))

        # Extract namespaces
        for ns in spec.get("namespaces") or []:
            result.nodes.append(
                GraphNode(
                    identifier=ns["name"],
                    name=ns["name"],
                    kind="namespace",
                    status="active",
                )
            )

        # Extract networks
        for net in spec.get("networks") or []:
            result.nodes.append(
                GraphNode(
                    identifier=net["name"],
                    name=net["name"],
                    kind="network",
                    status="active",
                )
            )

        # Build topology subgraphs
        for topo in spec.get("topology") or []:
            components = [c["resource"] for c in topo.get("components", [])]
            namespaces = [n["namespace"] for n in topo.get("namespaces") or []]
            result.topologies.append(
                GraphTopology(
                    name=topo["name"],
                    provisioner=topo.get("provisioner", ""),
                    provider=topo.get("provider", ""),
                    type=str(topo.get("type", "")),
                    components=components,
                    namespaces=namespaces,
                )
            )

        # Detect dangling references (depends_on targets not in resources)
        known_names = {n.identifier for n in result.nodes}
        for edge in result.edges:
            if edge.label == "depends_on" and edge.target not in known_names:
                result.nodes.append(
                    GraphNode(
                        identifier=edge.target,
                        name=edge.target,
                        kind="resource",
                        status="dangling",
                    )
                )

        self.logger.info(
            "Resource graph built",
            workspace=ws_name,
            resources=sum(1 for n in result.nodes if n.kind == "resource"),
            modules=sum(1 for n in result.nodes if n.kind == "module"),
        )
        return result

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _discover_entry_points(self) -> list[Path]:
        """Find deployment YAML files in the workspace."""
        if self._entry:
            entry_path = (self._work_path / self._entry).resolve()
            if entry_path.exists():
                return [entry_path]
            self._errors.append(f"Entry point not found: {self._entry}")
            return []

        # Scan for deployment files
        deployments: list[Path] = []
        for yaml_file in self._iter_yaml_files():
            data = self._parse_yaml(yaml_file)
            if data and data.get("kind") == "deployment":
                deployments.append(yaml_file)
        return deployments

    def _resolve_workspace_path(self) -> Optional[Path]:
        """Resolve the workspace file path from --entry or by discovery."""
        if self._entry:
            entry_path = (self._work_path / self._entry).resolve()
            if not entry_path.exists():
                return None
            data = self._parse_yaml(entry_path)
            if not data:
                return None
            # If entry is a workspace file, use it directly
            if data.get("kind") == "workspace":
                return entry_path
            # If entry is a deployment, resolve its workspace reference
            if data.get("kind") == "deployment":
                ws_ref = data.get("spec", {}).get("workspace", {}).get("file")
                if ws_ref:
                    ws_path = (entry_path.parent / ws_ref).resolve()
                    if ws_path.exists():
                        return ws_path
            return None

        # Discover first workspace file
        for yaml_file in self._iter_yaml_files():
            data = self._parse_yaml(yaml_file)
            if data and data.get("kind") == "workspace":
                return yaml_file
        return None

    def _walk_file(self, file_path: Path, result: GraphResult) -> None:
        """Recursively walk file references, building nodes and edges."""
        rel_path = str(file_path.relative_to(self._work_path)).replace("\\", "/")
        if rel_path in self._visited:
            return
        self._visited.add(rel_path)

        # Parse the file
        data = self._parse_yaml(file_path)
        if not data:
            # File exists but can't be parsed
            if file_path.exists():
                result.nodes.append(
                    GraphNode(
                        identifier=rel_path,
                        path=rel_path,
                        kind="unknown",
                        status="invalid",
                        errors=["Failed to parse YAML"],
                    )
                )
            else:
                result.nodes.append(
                    GraphNode(
                        identifier=rel_path,
                        path=rel_path,
                        kind="unknown",
                        status="missing",
                    )
                )
            return

        kind = data.get("kind", "unknown")
        name = data.get("meta", {}).get("name")

        # Determine validation status
        status = "valid"
        errors: list[str] = []
        if not self._no_validate:
            status, errors = self._validate_file(file_path)

        result.nodes.append(
            GraphNode(
                identifier=rel_path,
                path=rel_path,
                name=name,
                kind=kind,
                status=status,
                errors=errors,
            )
        )

        # Follow references based on kind
        spec = data.get("spec", {})
        if kind == "deployment":
            self._follow_deployment_refs(file_path, spec, rel_path, result)
        elif kind == "workspace":
            self._follow_workspace_refs(file_path, spec, rel_path, result)

    def _follow_deployment_refs(self, file_path: Path, spec: dict, source_rel: str, result: GraphResult) -> None:
        """Follow file references from a deployment spec."""
        # workspace
        ws_ref = spec.get("workspace", {}).get("file")
        if ws_ref:
            self._add_edge_and_walk(file_path, ws_ref, source_rel, "workspace", result)

        # environments (list of path strings)
        for env_path in spec.get("environments") or []:
            self._add_edge_and_walk(file_path, env_path, source_rel, "environment", result)

        # configurations
        for config in spec.get("configurations") or []:
            config_file = config.get("file")
            if config_file:
                self._add_edge_and_walk(file_path, config_file, source_rel, "configuration", result)

    def _follow_workspace_refs(self, file_path: Path, spec: dict, source_rel: str, result: GraphResult) -> None:
        """Follow file references from a workspace spec."""
        # resources
        for res in spec.get("resources") or []:
            res_file = res.get("file")
            if res_file:
                self._add_edge_and_walk(file_path, res_file, source_rel, "resource", result)
            # modules within resources
            for mod in res.get("modules") or []:
                mod_file = mod.get("file")
                if mod_file:
                    self._add_edge_and_walk(file_path, mod_file, source_rel, "module", result)

        # namespaces
        for ns in spec.get("namespaces") or []:
            ns_file = ns.get("file")
            if ns_file:
                self._add_edge_and_walk(file_path, ns_file, source_rel, "namespace", result)

        # networks
        for net in spec.get("networks") or []:
            net_file = net.get("file")
            if net_file:
                self._add_edge_and_walk(file_path, net_file, source_rel, "network", result)

        # firewalls
        for fw in spec.get("firewalls") or []:
            fw_file = fw.get("file")
            if fw_file:
                self._add_edge_and_walk(file_path, fw_file, source_rel, "firewall", result)

        # dns_zones
        for dns in spec.get("dns_zones") or []:
            dns_file = dns.get("file")
            if dns_file:
                self._add_edge_and_walk(file_path, dns_file, source_rel, "dns", result)

    def _add_edge_and_walk(
        self,
        source_file: Path,
        ref: str,
        source_rel: str,
        label: str,
        result: GraphResult,
    ) -> None:
        """Add an edge and recursively walk the target file."""
        # Handle @repo/ references
        if ref.startswith("@"):
            target_rel = ref
            result.edges.append(GraphEdge(source=source_rel, target=target_rel, label=label))
            if target_rel not in self._visited:
                self._visited.add(target_rel)
                result.nodes.append(
                    GraphNode(
                        identifier=target_rel,
                        path=target_rel,
                        kind=label,
                        status="external",
                    )
                )
            return

        # Resolve relative path
        target_path = (source_file.parent / ref).resolve()
        try:
            target_rel = str(target_path.relative_to(self._work_path)).replace("\\", "/")
        except ValueError:
            # File is outside workspace
            target_rel = ref
            result.edges.append(GraphEdge(source=source_rel, target=target_rel, label=label))
            if target_rel not in self._visited:
                self._visited.add(target_rel)
                result.nodes.append(
                    GraphNode(
                        identifier=target_rel,
                        path=target_rel,
                        kind=label,
                        status="external",
                    )
                )
            return

        result.edges.append(GraphEdge(source=source_rel, target=target_rel, label=label))

        if not target_path.exists():
            if target_rel not in self._visited:
                self._visited.add(target_rel)
                result.nodes.append(
                    GraphNode(
                        identifier=target_rel,
                        path=target_rel,
                        kind=label,
                        status="missing",
                    )
                )
        else:
            self._walk_file(target_path, result)

    def _find_orphan_files(self, result: GraphResult) -> None:
        """Find YAML files in workspace that aren't referenced by any other file."""
        referenced = {n.identifier for n in result.nodes}
        for yaml_file in self._iter_yaml_files():
            rel_path = str(yaml_file.relative_to(self._work_path)).replace("\\", "/")
            if rel_path not in referenced:
                data = self._parse_yaml(yaml_file)
                if data and data.get("apiVersion", "").startswith("strata"):
                    kind = data.get("kind", "unknown")
                    name = data.get("meta", {}).get("name")
                    result.nodes.append(
                        GraphNode(
                            identifier=rel_path,
                            path=rel_path,
                            name=name,
                            kind=kind,
                            status="orphan",
                        )
                    )

    def _validate_file(self, file_path: Path) -> tuple[str, list[str]]:
        """Validate a file and return (status, errors)."""
        from strata.controllers.lifecycle_controller import LifecycleController
        from strata.validators.platform_validator import PlatformValidator

        try:
            validator = PlatformValidator(file_path=file_path, lifecycle_controller=LifecycleController())
            if not validator.before_validate(self._work_path):
                return "invalid", validator.get_errors()
            if not validator.validate(self._work_path):
                return "invalid", validator.get_errors()
            return "valid", []
        except Exception as e:
            return "invalid", [str(e)]

    def _parse_yaml(self, file_path: Path) -> Optional[dict]:
        """Safely parse a YAML file, returning None on failure."""
        try:
            with open(file_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception:
            return None

    def _iter_yaml_files(self) -> list[Path]:
        """List all YAML files in the workspace (excluding hidden dirs)."""
        files: list[Path] = []
        for pattern in ("**/*.yaml", "**/*.yml"):
            for f in self._work_path.glob(pattern):
                # Skip hidden directories and common non-config paths
                parts = f.relative_to(self._work_path).parts
                if any(p.startswith(".") for p in parts):
                    continue
                files.append(f)
        return sorted(files)
