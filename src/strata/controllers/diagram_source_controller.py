#!/usr/bin/env python3
"""Resolve ``kind: diagram`` sources into the Jinja render context (ADR-0034).

This is the first box of the render pipeline::

    spec.sources[]  ->  Jinja context  ->  spec.template  ->  Mermaid  ->  SVG

Each entry in ``spec.sources`` is fetched from the workspace and bound into the
context under its ``as`` name.  Every resolver returns the same node/edge shape,
so templates written against one source read the same as templates written
against another, and the renderer downstream stays generic.

Node identity is carried by a structural ``strata://`` URI rather than a line
number, so a diagram keeps pointing at the right object when the YAML around it
is reformatted or reordered.  Resolving a URI to a concrete ``{file, line}`` is
a separate, on-demand operation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

import yaml

from strata.controllers.base_controller import BaseController
from strata.controllers.graph_controller import GraphController
from strata.controllers.policy_controller import PolicyController
from strata.controllers.promote_controller import PromoteController
from strata.controllers.solution_controller import SolutionController
from strata.models.diagram_model import DiagramSourceModel, DiagramSourceType
from strata.models.network_model import CidrSourceModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.services.dns_service import DnsService
from strata.services.environment_service import EnvironmentService
from strata.services.firewall_service import FirewallService
from strata.services.network_service import NetworkService
from strata.services.tenant_service import TenantService
from strata.utils.config import DEFAULT_BUILD_PATH
from strata.utils.drift_history import DriftHistoryStore
from strata.utils.graph import GraphResult, slugify_path
from strata.utils.strata_uri import FILE_KIND, build_uri
from strata.utils.system import resolve_path


def _file_uri(relative_path: str) -> str:
    """Durable identity for a workspace file."""
    return build_uri(FILE_KIND, relative_path)


def _workspace_object_uri(workspace_name: str, kind: str, name: str) -> str:
    """Durable identity for an object declared inside a workspace document."""
    return build_uri("workspace", workspace_name, kind, name)


# Audit log 'command' values the 'history' source surfaces — matches
# strata.commands.deploy.history_deploy_command._DEPLOY_OPERATIONS.
_DEPLOY_OPERATIONS = {"deploy_run", "deploy_destroy"}
_OPERATION_LABELS = {"deploy_run": "deploy run", "deploy_destroy": "deploy destroy"}

# Diagrams show recent history, not the whole archive — an unbounded 'history'
# or 'promotion' source would make an already-busy diagram unreadable.
_HISTORY_LIMIT = 20
_PROMOTION_LIMIT = 20

# Matches PromoteController's own path convention (.strata/promotions/records/),
# kept here rather than importing PromoteController's underscore-prefixed
# module constants across a controller boundary.
_PROMOTION_RECORDS_DIR = ".strata/promotions/records"

# Store types that resolve with no live integration contact — everything else
# (vault, bitwarden, azure-keyvault, ...) needs one. Deliberately never resolved
# here: this controller reports what is DECLARED, never a live-resolved value,
# so a diagram never triggers store contact and a 'secret' node never carries
# a real secret's contents.
_OFFLINE_STORES = {"constant", "environment", "github"}


class DiagramSourceController(BaseController):
    """Build the Jinja render context for a diagram from its declared sources."""

    def __init__(
        self,
        work_path: Path,
        entry: Optional[str] = None,
        no_validate: bool = False,
        configuration_service: Optional[ConfigurationService] = None,
    ) -> None:
        super().__init__()
        # Resolved so paths returned by GraphController (which resolves its own
        # copy) can be made relative to the same base in _relative().
        self._work_path = Path(work_path).resolve()
        self._entry = entry
        self._no_validate = no_validate
        # Injected by the command layer only when a diagram declares a 'policies'
        # source — every other source loads from a bare path with no profile
        # dependency, so this stays None for the common case.
        self._configuration_service = configuration_service
        self._resource_graph: Optional[GraphResult] = None
        self._workspace_document: Optional[Tuple[Path, Dict[str, Any]]] = None
        self._workspace_document_loaded = False
        self._deployment_document: Optional[Tuple[Path, Any]] = None
        self._deployment_document_loaded = False
        self._deployment_document_errors: List[str] = []
        self._resolved_environments: List[Tuple[Any, Path, List[str]]] = []
        self._resolved_environments_loaded = False
        self._deployment_build_path: Optional[Path] = None
        self._deployment_build_path_loaded = False
        self._repo_map: Dict[str, str] = {}
        self._repo_map_loaded = False
        self._resolvers: Dict[DiagramSourceType, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            DiagramSourceType.FILES: self._resolve_files,
            DiagramSourceType.TOPOLOGY: self._resolve_topology,
            DiagramSourceType.RESOURCES: self._resolve_resources,
            DiagramSourceType.MODULES: self._resolve_modules,
            DiagramSourceType.NAMESPACES: self._resolve_namespaces,
            DiagramSourceType.NETWORK: self._resolve_network,
            DiagramSourceType.FIREWALLS: self._resolve_firewalls,
            DiagramSourceType.DNS: self._resolve_dns,
            DiagramSourceType.STAGES: self._resolve_stages,
            DiagramSourceType.ENVIRONMENTS: self._resolve_environments,
            DiagramSourceType.TENANTS: self._resolve_tenants,
            DiagramSourceType.HISTORY: self._resolve_history,
            DiagramSourceType.PROMOTION: self._resolve_promotion,
            DiagramSourceType.APPROVALS: self._resolve_approvals,
            DiagramSourceType.VARIABLES: self._resolve_variables,
            DiagramSourceType.SECRETS: self._resolve_secrets,
            DiagramSourceType.FEATURES: self._resolve_features,
            DiagramSourceType.VALUES: self._resolve_values,
            DiagramSourceType.POLICIES: self._resolve_policies,
            DiagramSourceType.DRIFT: self._resolve_drift,
            DiagramSourceType.OUTPUTS: self._resolve_outputs,
            DiagramSourceType.LOCKS: self._resolve_locks,
            DiagramSourceType.REPOSITORIES: self._resolve_repositories,
            DiagramSourceType.SBOM: self._resolve_sbom,
        }

    @property
    def supported_types(self) -> List[str]:
        """Source types this controller can currently resolve."""
        return sorted(t.value for t in self._resolvers)

    def resolve(self, sources: Optional[Sequence[DiagramSourceModel]]) -> Dict[str, Any]:
        """Resolve *sources* into a context dict keyed by each source's bound name.

        Unsupported source types accumulate an error and bind an empty result, so
        one unimplemented source does not hide problems with the others.

        Args:
            sources: The diagram's ``spec.sources`` entries. ``None`` for a static
                diagram, which needs no context at all.

        Returns:
            Mapping of context name to ``{"nodes": [...], "edges": [...]}``.
        """
        context: Dict[str, Any] = {}
        for source in sources or []:
            resolver = self._resolvers.get(source.type)
            if resolver is None:
                self._add_error(
                    f"Diagram source type '{source.type.value}' is not implemented yet. "
                    f"Supported types: {self.supported_types}."
                )
                context[source.context_name] = {"nodes": [], "edges": []}
                continue
            context[source.context_name] = resolver(source.filter or {})
        return context

    # ─── Resolvers ────────────────────────────────────────────────────────────

    def _resolve_files(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the YAML file reference graph."""
        controller = GraphController(self._work_path, entry=self._entry, no_validate=self._no_validate)
        result = controller.build_file_graph()
        self._errors.extend(controller.get_errors())

        nodes = []
        for node in result.nodes:
            path = node.path or node.identifier
            nodes.append(
                {
                    "id": slugify_path(node.identifier),
                    "label": f"{node.name} ({path})" if node.name else path,
                    "kind": node.kind,
                    "status": node.status,
                    "uri": _file_uri(path),
                    # No line: a file node points at the document, not a position in it.
                    "location": {"file": path},
                    "errors": list(node.errors),
                }
            )
        return {
            "nodes": self._apply_filter(nodes, source_filter),
            "edges": self._edges(result),
            "entry_points": list(result.entry_points),
        }

    def _resolve_topology(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the logical resource topology graph — every kind, ungrouped by kind."""
        return self._resolve_topology_kind(source_filter, kind=None)

    def _resolve_resources(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve just the resource nodes of the topology graph.

        Includes dangling nodes (a ``depends_on`` target that names no real
        resource) — ``GraphController`` already gives those ``kind: "resource"``,
        since a dangling reference is still a resource-shaped problem to surface.
        """
        return self._resolve_topology_kind(source_filter, kind="resource")

    def _resolve_modules(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve just the module nodes of the topology graph."""
        return self._resolve_topology_kind(source_filter, kind="module")

    def _resolve_namespaces(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve just the namespace nodes of the topology graph."""
        return self._resolve_topology_kind(source_filter, kind="namespace")

    def _resolve_topology_kind(self, source_filter: Dict[str, Any], kind: Optional[str]) -> Dict[str, Any]:
        """Shared implementation behind ``topology`` and its single-kind views.

        Filtering by *kind* narrows which nodes are returned, but never prunes
        edges — same rule ``_apply_filter`` already applies to a user-supplied
        ``filter:``, so a ``resources``-only view can still show a ``depends_on``
        edge whose other end is a module, and a template can decide what to do
        with it.
        """
        result = self._get_resource_graph()

        workspace_file = result.entry_points[0] if result.entry_points else None

        nodes = []
        for node in result.nodes:
            if kind is not None and node.kind != kind:
                continue
            entry: Dict[str, Any] = {
                "id": slugify_path(node.identifier),
                "label": node.name or node.identifier,
                "kind": node.kind,
                "status": node.status,
                "metadata": dict(node.metadata),
            }
            # A dangling node names a resource that does not exist, so there is
            # no workspace object for a URI to point at.
            if node.status != "dangling" and result.workspace_name:
                entry["uri"] = _workspace_object_uri(result.workspace_name, node.kind, node.identifier)
                if workspace_file:
                    entry["location"] = {"file": workspace_file}
            nodes.append(entry)

        return {
            "nodes": self._apply_filter(nodes, source_filter),
            "edges": self._edges(result),
            "topologies": [
                {
                    "id": slugify_path(topo.name),
                    "name": topo.name,
                    "provisioner": topo.provisioner,
                    "provider": topo.provider,
                    "type": topo.type,
                    "components": [slugify_path(c) for c in topo.components],
                    "namespaces": [slugify_path(n) for n in topo.namespaces],
                }
                for topo in result.topologies
            ],
            "workspace": result.workspace_name,
        }

    def _resolve_network(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the network definitions referenced by ``spec.networks[]``.

        A workspace network reference points at a ``kind: network`` document
        that may itself declare several named networks — the individual
        network definitions are the natural node, not the reference wrapper.
        """
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []
        for ref_name, path in self._iter_workspace_refs("networks"):
            model = self._load_document(NetworkService, path, ref_name)
            if model is None:
                continue
            for net in model.spec.networks:
                nodes.append(
                    {
                        "id": slugify_path(net.name),
                        "label": net.name,
                        "kind": "network",
                        "status": "active",
                        "uri": build_uri("network", model.meta.name, "network", net.name),
                        "location": {"file": self._relative(path)},
                        "metadata": {
                            "address_space": [self._cidr_display(c) for c in net.address_space],
                            "subnet_count": len(net.subnets),
                            "peering_count": len(net.peerings or []),
                            "reference": ref_name,
                        },
                    }
                )
                for peering in net.peerings or []:
                    edges.append(
                        {
                            "source": slugify_path(net.name),
                            "target": slugify_path(peering.target),
                            "label": "peering",
                        }
                    )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": edges}

    def _resolve_firewalls(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the firewall rule sets referenced by ``spec.firewalls[]``.

        Individual rules carry no name of their own (``FirewallRuleModel`` has
        none), so the node is the referenced ruleset as a whole, summarised.
        """
        nodes: List[Dict[str, Any]] = []
        for ref_name, path in self._iter_workspace_refs("firewalls"):
            model = self._load_document(FirewallService, path, ref_name)
            if model is None:
                continue
            spec = model.spec
            nodes.append(
                {
                    "id": slugify_path(ref_name),
                    "label": ref_name,
                    "kind": "firewall",
                    "status": "active",
                    "uri": build_uri("firewall", model.meta.name),
                    "location": {"file": self._relative(path)},
                    "metadata": {
                        "allow_count": len(spec.allow or []),
                        "deny_count": len(spec.deny or []),
                        "default_count": len(spec.defaults or []),
                        "reset": bool(spec.reset),
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_dns(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the DNS zones referenced by ``spec.dns_zones[]``.

        Like ``network``, a reference points at a document that may declare
        several zones — the zone is the node, not the reference wrapper.
        """
        nodes: List[Dict[str, Any]] = []
        for ref_name, path in self._iter_workspace_refs("dns_zones"):
            model = self._load_document(DnsService, path, ref_name)
            if model is None:
                continue
            for zone in model.spec.zones:
                nodes.append(
                    {
                        "id": slugify_path(zone.name),
                        "label": zone.name,
                        "kind": "dns",
                        "status": "active",
                        "uri": build_uri("dns", model.meta.name, "zone", zone.name),
                        "location": {"file": self._relative(path)},
                        "metadata": {
                            "ttl": zone.ttl,
                            "record_count": len(zone.records or []),
                            "provider": model.spec.provider,
                            "reference": ref_name,
                        },
                    }
                )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_stages(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the deployment stages declared in ``spec.stages[]``.

        Status reflects nothing about a live run — stages are declarative here.
        Runtime progress belongs to the ``history`` source (deploy logs), not this one.
        """
        document = self._get_deployment_document()
        if document is None:
            return {"nodes": [], "edges": []}
        deployment_path, model = document

        nodes = []
        edges = []
        for stage in model.spec.stages or []:
            nodes.append(
                {
                    "id": slugify_path(stage.name),
                    "label": stage.name,
                    "kind": "stage",
                    "status": "active",
                    "uri": build_uri("deployment", model.meta.name, "stage", stage.name),
                    "location": {"file": self._relative(deployment_path)},
                    "metadata": {
                        "provisioner": stage.provisioner,
                        "topology": stage.topology,
                        "scope": stage.scope,
                        "on_failure": stage.on_failure,
                        "namespace": stage.namespace,
                    },
                }
            )
            for dep in stage.depends_on or []:
                edges.append({"source": slugify_path(stage.name), "target": slugify_path(dep), "label": "depends_on"})
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": edges}

    def _resolve_environments(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the environment files referenced by a deployment's ``spec.environments[]``.

        A bare-string entry carries no name of its own — the referenced document's
        ``meta.name`` is the node identity. The same environment referenced twice
        with different ``scope`` values (e.g. a per-wave override) is one node with
        every scope it was seen under, not a duplicate.
        """
        nodes = []
        for env_model, path, scopes in self._get_resolved_environments():
            spec = env_model.spec
            nodes.append(
                {
                    "id": slugify_path(env_model.meta.name),
                    "label": env_model.meta.name,
                    "kind": "environment",
                    "status": "active",
                    "uri": build_uri("environment", env_model.meta.name),
                    "location": {"file": self._relative(path)},
                    "metadata": {
                        "scopes": list(scopes),
                        "variable_count": len(spec.variables or []),
                        "secret_count": len(spec.secrets or []),
                        "feature_count": len(spec.features or []),
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_variables(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve declared (not resolved) variables from every referenced environment."""
        nodes = self._declaration_nodes("variables", "variable")
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_secrets(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve declared secrets — keys and store types only, never a value.

        Deliberately never calls ``ValueController.resolve_values()``: that
        method always attempts live store contact and returns the *actual*
        secret value in ``ResolvedValues.secrets``. A diagram source has no
        business doing either — it reads what ``spec.secrets[]`` declares and
        stops there.
        """
        nodes = self._declaration_nodes("secrets", "secret")
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_features(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve declared (not resolved) feature flags from every referenced environment."""
        nodes = self._declaration_nodes("features", "feature")
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_values(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve every declared variable, secret, and feature as one combined view."""
        nodes = (
            self._declaration_nodes("variables", "variable")
            + self._declaration_nodes("secrets", "secret")
            + self._declaration_nodes("features", "feature")
        )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _declaration_nodes(self, spec_attr: str, value_type: str) -> List[Dict[str, Any]]:
        """Project one of ``spec.variables[]`` / ``spec.secrets[]`` / ``spec.features[]``.

        Args:
            spec_attr: Attribute name on ``EnvironmentSpecModel`` (``"variables"``, etc.).
            value_type: Singular kind (``"variable"``, ``"secret"``, ``"feature"``) —
                also the ``strata://`` child-kind, matching the YAML collection key
                via the resolver's existing ``<kind>`` / ``<kind>s`` convention.

        Returns:
            One node per declared item across every environment the resolved
            deployment references, carrying its key, store type, and reachability
            (``offline`` if it resolves with no live contact, ``live`` otherwise).
            Never a value or a pointer to one, for any kind or store type — a
            'constant' today is one accidental edit away from holding a real
            secret, and a diagram is not the place to find out.
        """
        nodes: List[Dict[str, Any]] = []
        for env_model, path, _scopes in self._get_resolved_environments():
            for item in getattr(env_model.spec, spec_attr, None) or []:
                store = item.store.value if hasattr(item.store, "value") else str(item.store)
                nodes.append(
                    {
                        "id": slugify_path(f"{env_model.meta.name}_{item.key}"),
                        "label": item.key,
                        "kind": value_type,
                        "status": "offline" if store in _OFFLINE_STORES else "live",
                        "uri": build_uri("environment", env_model.meta.name, value_type, item.key),
                        "location": {"file": self._relative(path)},
                        "metadata": {"store": store, "environment": env_model.meta.name},
                    }
                )
        return nodes

    def _get_resolved_environments(self) -> List[Tuple[Any, Path, List[str]]]:
        """Resolve every distinct environment document the deployment references.

        Cached and shared by ``environments`` / ``variables`` / ``secrets`` /
        ``features`` / ``values`` — all five need the same environment set,
        just projected differently, and this runs the reference walk once
        regardless of how many of them a diagram declares together.
        """
        if not self._resolved_environments_loaded:
            by_name: Dict[str, Dict[str, Any]] = {}
            order: List[str] = []
            document = self._get_deployment_document()
            if document is not None:
                _deployment_path, model = document
                for ref in model.spec.environments or []:
                    # Resolved relative to the workspace root, not deployment_path's
                    # own directory — file references are always work_path-relative
                    # (matches BaseService._resolve_file_path()), with @repo_name/...
                    # references resolved through the merged solution + config repo map.
                    try:
                        path = resolve_path(str(self._work_path), ref.file, repo_map=self._get_repo_map()).resolve()
                    except ValueError as exc:
                        self._add_error(f"Environment reference '{ref.file}': {exc}")
                        continue
                    if not path.is_file():
                        self._add_error(f"Environment reference '{ref.file}' does not exist.")
                        continue
                    env_model = self._load_document(EnvironmentService, path, ref.file)
                    if env_model is None:
                        continue
                    name = env_model.meta.name
                    if name not in by_name:
                        by_name[name] = {"model": env_model, "path": path, "scopes": []}
                        order.append(name)
                    if ref.scope and ref.scope not in by_name[name]["scopes"]:
                        by_name[name]["scopes"].append(ref.scope)
            self._resolved_environments = [
                (by_name[name]["model"], by_name[name]["path"], by_name[name]["scopes"]) for name in order
            ]
            self._resolved_environments_loaded = True
        return self._resolved_environments

    def _resolve_policies(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve policies declared in ``configuration.spec.policies``.

        Unlike every other source, this one needs an active profile's
        configuration already loaded — there is no bare file path to read
        policies from (they live only in the singleton ``ConfigurationService``,
        populated by the command layer). When the command hasn't loaded one
        (most diagrams never need to), this reports why rather than silently
        returning an empty policy list indistinguishable from "none declared".
        """
        config_service = self._configuration_service or ConfigurationService.get_instance()
        if config_service.model is None:
            self._add_error(
                "Diagram source 'policies' requires an active profile's configuration to be "
                "loaded. Run 'strata profile activate <name>' first."
            )
            return {"nodes": [], "edges": []}

        policies = PolicyController().get_declared_policies(config_service)
        configuration_name = config_service.model.meta.name

        nodes = []
        for policy in policies:
            name = str(policy.name)
            nodes.append(
                {
                    "id": slugify_path(name),
                    "label": name,
                    "kind": "policy",
                    "status": "enabled" if policy.enabled else "disabled",
                    "uri": build_uri("configuration", configuration_name, "policy", name),
                    "metadata": {
                        "type": policy.type,
                        "phase": policy.phase,
                        "enforcement": policy.enforcement,
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_drift(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve tracked drift entries from ``.strata/drift/{deployment}.drift.json``.

        Pure history-file read — never runs a live ``deploy drift`` check.  An
        entry is 'drifting' when it appeared in the most recent recorded run,
        'acknowledged' when suppressed on purpose, otherwise 'resolved' (it drifted
        at some point but not on the last run). Like ``history``, an entry has no
        workspace object behind it, so nodes carry no ``uri``/``location``.
        """
        document = self._get_deployment_document()
        if document is None:
            return {"nodes": [], "edges": []}
        _path, model = document
        deployment_name = str(model.meta.name)

        history = DriftHistoryStore(self._work_path, deployment_name)
        history.load()

        recent_runs = history.list_runs(last=1)
        currently_drifting = set(recent_runs[0]["addresses"]) if recent_runs else set()

        nodes = []
        for entry in history.list_entries():
            address = entry["address"]
            if entry.get("acknowledged"):
                status = "acknowledged"
            elif address in currently_drifting:
                status = "drifting"
            else:
                status = "resolved"
            nodes.append(
                {
                    "id": slugify_path(address),
                    "label": address,
                    "kind": "drift",
                    "status": status,
                    "metadata": {
                        "first_detected": entry.get("first_detected"),
                        "last_detected": entry.get("last_detected"),
                        "consecutive_checks": entry.get("consecutive_checks", 0),
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_outputs(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve declared Terraform output keys from the cached ``deployment-outputs.json``.

        Reads the artifact produced by the last successful ``deploy run`` — never
        triggers a live ``terraform output`` call. Node metadata carries only the
        output's *key*, its owning stage, and whether it is sensitive — never the
        value.  This is deliberate and absolute: the underlying cache file is not
        reliably guaranteed to have masked sensitive values on every code path that
        can write it, so this resolver never reads the value field at all, from any
        stage's output map.
        """
        build_path = self._get_deployment_build_path()
        if build_path is None:
            return {"nodes": [], "edges": []}

        outputs_file = build_path / "deployment-outputs.json"
        if not outputs_file.is_file():
            self._add_error(f"No outputs artifact at {self._relative(outputs_file)}. Run 'strata deploy run' first.")
            return {"nodes": [], "edges": []}

        try:
            data = json.loads(outputs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._add_error(f"Failed to read {self._relative(outputs_file)}: {exc}")
            return {"nodes": [], "edges": []}

        sensitive_paths = set(data.get("sensitive_keys") or [])
        dotted_keys: Dict[str, str] = {}
        for stage_name, stage_outputs in (data.get("outputs") or {}).items():
            for key in stage_outputs:
                dotted_keys[f"{stage_name}.{key}"] = stage_name
        for dotted in sensitive_paths:
            stage_name, _, key = dotted.partition(".")
            dotted_keys.setdefault(dotted, stage_name)

        nodes = []
        for dotted, stage_name in dotted_keys.items():
            key = dotted[len(stage_name) + 1 :] if dotted.startswith(f"{stage_name}.") else dotted
            nodes.append(
                {
                    "id": slugify_path(dotted),
                    "label": key,
                    "kind": "output",
                    "status": "sensitive" if dotted in sensitive_paths else "available",
                    "metadata": {"stage": stage_name},
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_locks(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve the deployment's declared ``spec.locking`` policy.

        Reports only what is *declared* — never a live "is a lock currently held"
        check against the backend, which would need network/auth access that
        differs per backend type (``azurerm``, ``terraform_cloud``, ``s3``, ...).
        """
        document = self._get_deployment_document()
        if document is None:
            return {"nodes": [], "edges": []}
        deployment_path, model = document
        locking = model.spec.locking
        if locking is None:
            return {"nodes": [], "edges": []}

        node = {
            "id": slugify_path(f"{model.meta.name}_lock"),
            "label": f"{model.meta.name} locking",
            "kind": "lock",
            "status": "enabled" if locking.enabled else "disabled",
            "location": {"file": self._relative(deployment_path)},
            "metadata": {
                "strategy": locking.strategy,
                "wait_timeout": locking.wait_timeout,
                "force_unlock_after": locking.force_unlock_after,
            },
        }
        return {"nodes": self._apply_filter([node], source_filter), "edges": []}

    def _resolve_repositories(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve repositories declared in ``.strata/solution.json``.

        Declaration only — status reflects whether the local path exists on disk,
        never a live ``git fetch``/``git status`` call. Repository URLs sometimes
        embed credentials (``https://user:token@host/...``); any userinfo is
        stripped before the URL is surfaced.
        """
        controller = SolutionController(self._work_path)
        ok, load_errors = controller.load()
        if not ok:
            self._add_error(
                "Diagram source 'repositories' requires an initialized workspace "
                "('.strata/solution.json'). Run 'strata sln init' first."
            )
            return {"nodes": [], "edges": []}

        repos, repo_errors = controller.get_repositories()
        self._errors.extend(repo_errors)

        nodes = []
        for repo in repos:
            local_path = (self._work_path / repo.path) if str(repo.type) == "gitops" else Path(str(repo.url))
            if not local_path.is_absolute():
                local_path = (self._work_path / local_path).resolve()
            nodes.append(
                {
                    "id": slugify_path(str(repo.name)),
                    "label": str(repo.name),
                    "kind": "repository",
                    "status": "active" if local_path.exists() else "missing",
                    "metadata": {
                        "type": repo.type,
                        "url": self._redact_url_credentials(repo.url),
                        "path": repo.path,
                        "branch": repo.branch,
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_sbom(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve components from the cached ``sbom.json`` (CycloneDX 1.6).

        Reads the artifact produced by ``strata build sbom`` / ``build run`` —
        never triggers a fresh scan. Component identity (name, version, purl) is
        package metadata, not a secret, so it is shown in full — unlike
        variables/secrets/features/values/outputs, which never show a value.
        """
        build_path = self._get_deployment_build_path()
        if build_path is None:
            return {"nodes": [], "edges": []}

        sbom_file = build_path / "sbom.json"
        if not sbom_file.is_file():
            self._add_error(
                f"No SBOM at {self._relative(sbom_file)}. Run 'strata build sbom' (or 'strata build run') first."
            )
            return {"nodes": [], "edges": []}

        try:
            data = json.loads(sbom_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._add_error(f"Failed to read {self._relative(sbom_file)}: {exc}")
            return {"nodes": [], "edges": []}

        nodes = []
        for index, component in enumerate(data.get("components") or []):
            name = component.get("name", "?")
            version = component.get("version")
            purl = component.get("purl")
            component_type = component.get("type", "component")
            properties = {p.get("name"): p.get("value") for p in component.get("properties") or []}
            nodes.append(
                {
                    "id": slugify_path(purl or f"{name}_{index}"),
                    "label": f"{name}@{version}" if version else name,
                    "kind": component_type,
                    "status": "listed",
                    "metadata": {
                        "version": version,
                        "purl": purl,
                        "properties": properties,
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_tenants(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve every ``kind: tenant`` document found anywhere in the workspace.

        Tenants have no workspace-level reference list to walk (unlike network/
        firewall/dns) — the convention `docs/config/deployment.md` describes
        (``tenants/<code>.yaml``) is a default, not a rule enforced anywhere, so a
        full-tree scan is the only approach that works regardless of where a given
        workspace actually keeps its tenant files.
        """
        controller = GraphController(self._work_path, entry=self._entry, no_validate=self._no_validate)
        active_code = self._active_tenant_code()

        nodes = []
        for path in controller.iter_yaml_files():
            data = controller.parse_yaml(path)
            if not data or data.get("kind") != "tenant":
                continue
            model = self._load_document(TenantService, path, path.name)
            if model is None:
                continue
            spec = model.spec
            nodes.append(
                {
                    "id": slugify_path(model.meta.name),
                    "label": model.meta.name,
                    "kind": "tenant",
                    "status": "active",
                    "uri": build_uri("tenant", model.meta.name),
                    "location": {"file": self._relative(path)},
                    "metadata": {
                        "code": spec.code,
                        "zones": list(spec.zones),
                        "environment_count": len(spec.environments or []),
                        # True when this is the tenant the resolved deployment (if
                        # any) is running as — lets a template highlight "the one
                        # in use" among every tenant declared in the workspace.
                        "active": spec.code == active_code,
                    },
                }
            )
        self._errors.extend(controller.get_errors())
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_history(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve recent deploy run/destroy executions from ``.strata/logs/``.

        Entries come from the plain audit trail, not a ``kind: <x>`` document —
        there is no workspace object behind one, so nodes carry no ``uri`` or
        ``location``, the same way the ADR treats drift entries.
        """
        controller = SolutionController(self._work_path)
        ok, entries, errors = controller.get_logs(work_path=self._work_path, lines=_HISTORY_LIMIT * 20)
        self._errors.extend(errors)
        if not ok:
            return {"nodes": [], "edges": []}

        deploy_events = [e for e in entries if e.get("command") in _DEPLOY_OPERATIONS]

        # Group by execution_id, keeping the last entry seen (carries the final
        # success flag) — mirrors HistoryDeployCommand's own grouping exactly.
        by_execution: Dict[str, Dict[str, Any]] = {}
        for event in deploy_events:
            execution_id = event.get("execution_id", "")
            if execution_id:
                by_execution[execution_id] = event

        nodes: List[Dict[str, Any]] = []
        for execution_id, event in by_execution.items():
            timestamp = event.get("timestamp", "")
            try:
                when = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                when = timestamp[:16] if timestamp else "?"

            operation = event.get("command", "?")
            success = event.get("success")
            nodes.append(
                {
                    "id": slugify_path(execution_id),
                    "label": f"{_OPERATION_LABELS.get(operation, operation)} · {when}",
                    "kind": "history",
                    "status": "success" if success else ("failed" if success is False else "unknown"),
                    "metadata": {
                        "execution_id": execution_id,
                        "operation": operation,
                        "when": when,
                        "file": event.get("file", ""),
                        "stage": event.get("stage", ""),
                    },
                }
            )

        nodes.sort(key=lambda n: n["metadata"]["when"], reverse=True)
        nodes = nodes[:_HISTORY_LIMIT]
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_promotion(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve completed promotion records from ``.strata/promotions/records/``.

        One node per record, newest first. ``outcome`` is the model's own
        ``completed``/``partial``/``rolled-back`` values — the design-token
        registry aliases these to the same ramp as ``success``/``partial``/``failed``.
        """
        controller = PromoteController()
        records = controller.get_history(self._work_path, last=_PROMOTION_LIMIT)

        nodes = []
        for record in records:
            name = record["name"]
            nodes.append(
                {
                    "id": slugify_path(name),
                    "label": f"{record['target']} \u2192 {record['to_version']} ({record['ring']})",
                    "kind": "promotion",
                    "status": record["outcome"],
                    "uri": build_uri("promotion-record", name),
                    "location": {"file": f"{_PROMOTION_RECORDS_DIR}/{name}.yaml"},
                    "metadata": {
                        "target": record["target"],
                        "from_version": record["from_version"],
                        "to_version": record["to_version"],
                        "ring": record["ring"],
                        "initiated_by": record["initiated_by"],
                        "started_at": record["started_at"],
                        "completed_at": record["completed_at"],
                    },
                }
            )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _resolve_approvals(self, source_filter: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve gate results recorded on completed promotion records.

        Gate results live inside ``spec.gates[]`` on the same record ``promotion``
        reads — no separate live evaluation, no separate file to read.
        """
        controller = PromoteController()
        records = controller.get_history(self._work_path, last=_PROMOTION_LIMIT)

        nodes = []
        for record in records:
            name = record["name"]
            record_path = self._work_path / _PROMOTION_RECORDS_DIR / f"{name}.yaml"
            try:
                raw = yaml.safe_load(record_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._add_error(f"Promotion record '{name}' ({_PROMOTION_RECORDS_DIR}/{name}.yaml): {exc}")
                continue
            for gate in (raw or {}).get("spec", {}).get("gates") or []:
                gate_name = gate.get("gate", "?")
                passed = bool(gate.get("passed"))
                nodes.append(
                    {
                        "id": slugify_path(f"{name}_{gate_name}_{gate.get('ring', '')}"),
                        "label": f"{gate_name} ({gate.get('ring', '?')})",
                        "kind": "approval",
                        "status": "passed" if passed else "failed",
                        "uri": build_uri("promotion-record", name, "gate", gate_name),
                        "location": {"file": f"{_PROMOTION_RECORDS_DIR}/{name}.yaml"},
                        "metadata": {
                            "promotion": name,
                            "require": gate.get("require"),
                            "checked_at": gate.get("checked_at"),
                            "detail": gate.get("detail"),
                        },
                    }
                )
        return {"nodes": self._apply_filter(nodes, source_filter), "edges": []}

    def _active_tenant_code(self) -> Optional[str]:
        """Best-effort ``spec.tenant`` of the resolved deployment, or ``None``.

        Tenants must resolve even with no deployment entry at all (list every
        tenant in the workspace) — so a deployment-resolution failure here is
        silently ignored rather than surfaced as an error.
        """
        document = self._get_deployment_document(record_errors=False)
        if document is None:
            return None
        _path, model = document
        return model.spec.tenant

    def _get_deployment_document(self, record_errors: bool = True) -> Optional[Tuple[Path, Any]]:
        """Resolve and validate the deployment file once per controller instance.

        Errors from the *actual* resolution attempt are captured once and cached
        in ``_deployment_document_errors``, regardless of which caller triggered
        it — ``_active_tenant_code()`` calls with ``record_errors=False`` so a
        missing/invalid deployment doesn't fail the ``tenants`` source (which
        must still work with no deployment entry at all). A later caller that
        does want them (``stages`` / ``environments``) still sees them, exactly
        once, whether or not the tenant lookup ran first.
        """
        if not self._deployment_document_loaded:
            errors_before = len(self._errors)
            controller = GraphController(self._work_path, entry=self._entry, no_validate=self._no_validate)
            resolved = controller.resolve_deployment()
            if resolved is None:
                self._errors.extend(controller.get_errors())
            else:
                deployment_path, _data = resolved
                model = self._load_document(DeploymentService, deployment_path, deployment_path.name)
                self._deployment_document = (deployment_path, model) if model is not None else None
            self._deployment_document_errors = list(self._errors[errors_before:])
            del self._errors[errors_before:]
            self._deployment_document_loaded = True

        if record_errors:
            for error in self._deployment_document_errors:
                if error not in self._errors:
                    self._errors.append(error)
        return self._deployment_document

    def _get_build_path_base(self) -> Path:
        """Return the workspace's base build directory (before deployment-versioning).

        Mirrors ``BaseCommand._get_build_path()``: prefers the configuration
        service's resolved path when one was injected (only for 'policies' today),
        otherwise falls back to ``work_path / DEFAULT_BUILD_PATH``.
        """
        if self._configuration_service is not None:
            return self._configuration_service.get_default_build_path(self._work_path, create_path=False)
        return self._work_path / DEFAULT_BUILD_PATH

    def _get_deployment_build_path(self) -> Optional[Path]:
        """Resolve the deployment's own build directory once per controller instance.

        Mirrors ``DeploymentService.get_build_path()``: ``base_build_path /
        {name}-{version}``. Used by ``outputs``/``sbom`` to locate cached artifacts
        without a live build/deploy call.
        """
        if not self._deployment_build_path_loaded:
            path = None
            document = self._get_deployment_document()
            if document is not None:
                _dep_path, model = document
                labels = model.meta.labels
                version = labels.get("version") if isinstance(labels, dict) else None
                if not version:
                    self._add_error(
                        f"Deployment '{model.meta.name}' has no meta.labels.version — "
                        f"cannot locate its build directory."
                    )
                else:
                    path = self._get_build_path_base() / f"{model.meta.name}-{version}"
            self._deployment_build_path = path
            self._deployment_build_path_loaded = True
        return self._deployment_build_path

    def _get_resource_graph(self) -> GraphResult:
        """Build the resource topology graph once per controller instance.

        A diagram commonly declares more than one of ``topology`` / ``resources`` /
        ``modules`` / ``namespaces`` at once (an overview plus a detail view) —
        without this, each would independently re-parse and re-validate every
        workspace YAML file.
        """
        if self._resource_graph is None:
            controller = GraphController(self._work_path, entry=self._entry, no_validate=self._no_validate)
            self._resource_graph = controller.build_resource_graph()
            self._errors.extend(controller.get_errors())
        return self._resource_graph

    def _get_repo_map(self) -> Dict[str, str]:
        """Return the merged repo_map for resolving ``@repo_name/...`` file references.

        Solution-level repos (``.strata/solution.json``) take precedence over
        configuration-level remotes — matches ``DeploymentService._merged_repo_map()``.
        Cached: every source that resolves a cross-repo reference shares one lookup
        instead of re-reading solution.json per reference.
        """
        if not self._repo_map_loaded:
            config_map: Dict[str, str] = {}
            config_service = self._configuration_service or ConfigurationService.get_instance()
            if config_service.model is not None:
                config_map = config_service.get_remote_map()

            solution_map: Dict[str, str] = {}
            controller = SolutionController(self._work_path)
            ok, _errors = controller.load()
            if ok:
                solution_map = controller.get_repo_map()

            self._repo_map = {**config_map, **solution_map}
            self._repo_map_loaded = True
        return self._repo_map

    def _get_workspace_document(self) -> Optional[Tuple[Path, Dict[str, Any]]]:
        """Resolve the workspace file once per controller instance.

        ``None`` is itself a valid, cacheable outcome (no workspace found) —
        a separate ``_workspace_document_loaded`` flag distinguishes "not tried
        yet" from "tried and found nothing", so a missing workspace is not
        re-searched (and re-erred on) once per network/firewall/dns source.
        """
        if not self._workspace_document_loaded:
            controller = GraphController(self._work_path, entry=self._entry, no_validate=self._no_validate)
            self._workspace_document = controller.resolve_workspace()
            self._errors.extend(controller.get_errors())
            self._workspace_document_loaded = True
        return self._workspace_document

    def _iter_workspace_refs(self, key: str) -> List[Tuple[str, Path]]:
        """List ``(reference_name, resolved_file_path)`` pairs for ``spec.<key>[]``.

        Cross-repository ``@repo/path`` references are resolved through the merged
        solution + configuration repo map (see :meth:`_get_repo_map`).
        """
        document = self._get_workspace_document()
        if document is None:
            return []
        _ws_path, data = document

        refs: List[Tuple[str, Path]] = []
        for entry in data.get("spec", {}).get(key) or []:
            name = entry.get("name")
            file_ref = entry.get("file")
            if not name or not file_ref:
                continue
            # Resolved relative to the workspace root, not ws_path's own
            # directory — file references are always work_path-relative
            # (matches BaseService._resolve_file_path()), with @repo_name/...
            # references resolved through the merged solution + config repo map.
            try:
                resolved = resolve_path(str(self._work_path), file_ref, repo_map=self._get_repo_map()).resolve()
            except ValueError as exc:
                self._add_error(f"'{name}' references '{file_ref}': {exc}")
                continue
            if not resolved.is_file():
                self._add_error(f"'{name}' references '{file_ref}', which does not exist.")
                continue
            refs.append((name, resolved))
        return refs

    def _load_document(self, service_class: type, path: Path, ref_name: str) -> Optional[Any]:
        """Load and validate one referenced document, reporting failure per-reference.

        One invalid network/firewall/dns file must not stop the others from
        rendering — the same "don't hide problems with the others" rule
        :meth:`resolve` already applies across whole source types.
        """
        service = service_class(str(path))
        is_valid, errors = service.validate()
        if not is_valid:
            for error in errors:
                self._add_error(f"'{ref_name}' ({self._relative(path)}): {error}")
            return None
        return service.get_model()

    def _relative(self, path: Path) -> str:
        """Render *path* relative to the workspace root for display."""
        try:
            return str(path.relative_to(self._work_path)).replace("\\", "/")
        except ValueError:
            return str(path)

    @staticmethod
    def _redact_url_credentials(url: str) -> str:
        """Strip any embedded ``user:pass@`` userinfo from a repository URL.

        Git remote URLs sometimes carry a token or credential in the userinfo
        component (``https://x-access-token:...@github.com/...``) — this is never
        shown in a diagram, regardless of the repository's own configuration.
        """
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        if "@" not in parts.netloc:
            return url
        _, _, host = parts.netloc.rpartition("@")
        return urlunsplit(parts._replace(netloc=host))

    @staticmethod
    def _cidr_display(cidr: "CidrSourceModel") -> str:
        """Render a CIDR union field for display without resolving it.

        A CIDR may come from a literal value, a variable, or a secret — the
        diagram shows which, rather than needing the value resolved.
        """
        if cidr.value is not None:
            return cidr.value
        if cidr.var is not None:
            return f"var:{cidr.var}"
        return f"secret:{cidr.secret}"

    # ─── Shared helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _edges(result: GraphResult) -> List[Dict[str, str]]:
        """Project graph edges onto slugified node ids."""
        return [
            {
                "source": slugify_path(edge.source),
                "target": slugify_path(edge.target),
                "label": edge.label,
            }
            for edge in result.edges
        ]

    @staticmethod
    def _apply_filter(nodes: List[Dict[str, Any]], source_filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Keep only nodes matching every key in *source_filter*.

        A list value matches any of its entries, so ``status: [invalid, missing]``
        reads naturally. Keys the node does not carry never match — a filter that
        selects nothing is a visible empty diagram rather than a silent no-op.
        """
        if not source_filter:
            return nodes

        def matches(node: Dict[str, Any]) -> bool:
            for key, expected in source_filter.items():
                actual = node.get(key)
                if isinstance(expected, list):
                    if actual not in expected:
                        return False
                elif actual != expected:
                    return False
            return True

        return [node for node in nodes if matches(node)]
