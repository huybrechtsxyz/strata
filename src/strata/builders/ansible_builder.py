"""Build Ansible YAML variable files from the generated platform model.

Mirrors the TerraformBuilder pattern: reads the assembled platform artifact
(``platform.json``) and projects it into focused, Ansible-native YAML variable
files that playbooks can consume via ``--extra-vars @file.yml``.

Security: this builder NEVER writes resolved variable/feature/secret values.
It only documents required keys so playbooks know what to expect at deploy time.

Output files (written to ``{provisioner_build_path}/``):

| File                         | Top-level key          | Ansible usage                                           |
| ---------------------------- | ---------------------- | ------------------------------------------------------- |
| ``strata_workspace.yml``     | ``strata_workspace``   | ``{{ strata_workspace.name }}``                         |
| ``strata_providers.yml``     | ``strata_providers``   | ``{{ strata_providers[name] }}``                        |
| ``strata_topologies.yml``    | ``strata_topologies``  | ``loop: {{ strata_topologies[topo].components }}``      |
| ``strata_resources.yml``     | ``strata_resources``   | ``loop: {{ strata_resources | dict2items }}``           |
| ``strata_resx_<type>.yml``   | ``strata_<type>``      | ``loop: {{ strata_objectstorage | dict2items }}``       |
| ``strata_modules.yml``       | ``strata_modules``     | ``{{ strata_modules[name] }}``                          |
| ``strata_namespaces.yml``    | ``strata_namespaces``  | ``{{ strata_namespaces[name] }}``                       |
| ``strata_firewalls.yml``     | ``strata_firewalls``   | ``{{ strata_firewalls[name].rules }}``                  |
| ``strata_dns.yml``           | ``strata_dns_zones``   | ``{{ strata_dns_zones[name].zones }}``                  |
| ``strata_networks.yml``      | ``strata_networks``    | ``{{ strata_networks[name] }}``                         |
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml

from strata.builders.base_builder import BaseBuilder
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class AnsibleBuilder(BaseBuilder):
    """Builder for Ansible YAML variable file generation.

    Reads ``platform.json`` (the assembled platform artifact) and writes
    focused YAML variable files into each ansible provisioner's build
    directory.  The ``AnsibleDeployer`` discovers these files at deploy
    time and passes them as ``--extra-vars @file.yml`` arguments.
    """

    # Prefix used for all generated variable files (enables glob discovery).
    FILE_PREFIX = "strata_"

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Validate that the deployment service is ready."""
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        if not dry_run:
            platform_path = (
                solution_controller.get_platform_path(deployment_service, build_path)
                if solution_controller is not None
                else deployment_service.get_build_path(build_path) / "platform.json"
            )
            if not platform_path.exists():
                self._errors.append(f"Platform model not found at: {platform_path}. Run platform build first.")
                return False

        if self.verbose:
            self._messages.append("Pre-build validation passed for Ansible artifacts")

        return True

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        platform_model: Optional[PlatformArtifactModel] = None,
        solution_controller: Optional["SolutionController"] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Build Ansible YAML variable files from ``platform.json``.

        Args:
            deployment_service: Loaded deployment service.
            work_path: Workspace root directory.
            build_path: Build output directory path.
            dry_run: When True, plan but don't write files.
            platform_model: Pre-assembled model (avoids disk read in dry-run).
            solution_controller: Optional controller for canonical path helpers.

        Returns:
            True on success, False on failure.
        """
        try:
            if platform_model is not None:
                if self.verbose:
                    model_name = (
                        platform_model.meta.name
                        if platform_model and getattr(platform_model, "meta", None)
                        else "<unknown>"
                    )
                    self._messages.append(f"Using pre-assembled platform model: {model_name}")
            else:
                platform_path = (
                    solution_controller.get_platform_path(deployment_service, build_path)
                    if solution_controller is not None
                    else deployment_service.get_build_path(build_path) / "platform.json"
                )

                if not platform_path.exists():
                    self._errors.append("Platform model not found. Run platform build first.")
                    return False

                platform_service = PlatformService.load(str(platform_path), validate=True)
                if not platform_service.is_validated() or not platform_service.model:
                    self._errors.append("Platform model validation failed")
                    return False

                platform_model = platform_service.model
                if self.verbose and platform_model and getattr(platform_model, "meta", None):
                    self._messages.append(f"Loaded platform model: {platform_model.meta.name}")

            if platform_model is None:
                self._errors.append("Platform model is None after loading")
                return False

            ansible_vars = self._build_ansible_vars(platform_model, [])

            if dry_run:
                ansible_paths = self._resolve_ansible_paths(deployment_service, build_path, solution_controller)
                ansible_path = (
                    ansible_paths[0] if ansible_paths else deployment_service.get_build_path(build_path) / "ansible"
                )
                planned = self._get_planned_files(ansible_vars)
                for filename in planned:
                    self._messages.append(f"[DRY-RUN] Would write: {ansible_path / filename}")
                self._messages.append(f"[DRY-RUN] Planned {len(planned)} Ansible variable file(s)")

                # Validate source copy in dry-run mode (no writes)
                if not self._copy_provisioner_source(
                    deployment_service=deployment_service,
                    build_path=build_path,
                    work_path=work_path,
                    repo_map=repo_map or {},
                    dry_run=True,
                    solution_controller=solution_controller,
                ):
                    return False

                return True

            self._messages.extend(
                self._save_ansible_vars(ansible_vars, deployment_service, build_path, solution_controller)
            )

            # Copy ansible source files from each provisioner's source_path into the build
            if not self._copy_provisioner_source(
                deployment_service=deployment_service,
                build_path=build_path,
                work_path=work_path,
                repo_map=repo_map or {},
                dry_run=False,
                solution_controller=solution_controller,
            ):
                return False

            return True

        except Exception as exc:
            error_msg = f"Failed to build Ansible artifacts: {exc}"
            self.logger.exception("Failed to build Ansible artifacts", error=str(exc))
            self._errors.append(error_msg)
            return False

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Verify generated files exist."""
        if dry_run:
            if self.verbose:
                self._messages.append("[DRY-RUN] Skipping Ansible artifact file-existence check")
            return True

        ansible_paths = self._resolve_ansible_paths(deployment_service, build_path, solution_controller)
        if not ansible_paths:
            ansible_paths = [deployment_service.get_build_path(build_path) / "ansible"]

        for ansible_path in ansible_paths:
            if not ansible_path.exists():
                # No ansible provisioners — nothing to check
                continue
            generated = list(ansible_path.glob(f"{self.FILE_PREFIX}*.yml"))
            if not generated:
                self._errors.append(f"No Ansible variable files found in: {ansible_path}")
                return False
            if self.verbose:
                self._messages.append(f"Ansible artifacts created at: {ansible_path} ({len(generated)} files)")

        return True

    # ------------------------------------------------------------------
    # Variable assembly (mirrors TerraformBuilder._build_terraform_vars)
    # ------------------------------------------------------------------

    def _build_ansible_vars(
        self,
        platform: PlatformArtifactModel,
        messages: List[str],
    ) -> Dict[str, Any]:
        """Build all Ansible variable payloads."""
        return {
            "workspace": self._build_workspace_vars(platform, messages),
            "providers": self._build_provider_vars(platform, messages),
            "topologies": self._build_topology_vars(platform, messages),
            "resources": self._build_resource_vars(platform, messages),
            "resources_by_type": self._build_resources_by_type(platform, messages),
            "modules": self._build_module_vars(platform, messages),
            "namespaces": self._build_namespace_vars(platform, messages),
            "firewalls": self._build_firewall_vars(platform, messages),
            "dns": self._build_dns_vars(platform, messages),
            "networks": self._build_network_vars(platform, messages),
            "Tenant": self._build_tenant_vars(platform, messages),
        }

    def _build_workspace_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build workspace-level variable payload."""
        workspace = platform.spec.workspace
        workspace_labels = workspace.labels or {}
        deployment_labels = platform.meta.labels or {}

        workspace_version = workspace_labels.get("version", "1.0.0")
        deployment_version = deployment_labels.get("version", workspace_version)
        environment = deployment_labels.get("environment", "production")

        payload = {
            "strata_workspace": {
                "name": str(workspace.name),
                "version": workspace_version,
                "deployment_name": str(platform.meta.name),
                "environment": environment,
                "platform_version": getattr(platform.apiVersion, "value", str(platform.apiVersion)),
                "labels": workspace_labels,
                "metadata": {
                    "deployment_version": deployment_version,
                    "workspace_description": (
                        workspace.annotations.get("description", "") if workspace.annotations else ""
                    ),
                    "deployment_description": (
                        platform.meta.annotations.get("description", "") if platform.meta.annotations else ""
                    ),
                    "workspace_tags": workspace.tags or [],
                    "deployment_tags": platform.meta.tags or [],
                },
            }
        }

        if self.verbose:
            messages.append(f"Built workspace vars: {workspace.name}")

        return payload

    def _build_provider_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build provider variable payload."""
        providers_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.providers:
            for provider in platform.spec.providers:
                providers_dict[str(provider.name)] = {
                    "type": provider.properties.type,
                    "region": provider.properties.region,
                    "version": provider.properties.version,
                    "description": provider.description,
                    "labels": provider.labels or {},
                    "tags": provider.tags or [],
                }

        if self.verbose:
            messages.append(f"Built provider vars: {len(providers_dict)} providers")

        return {"strata_providers": providers_dict}

    def _build_topology_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build topology variable payload."""
        topologies_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.topologies:
            for topology in platform.spec.topologies:
                components = []
                for component in topology.components:
                    entry: Dict[str, Any] = {"resource": component.resource}
                    if getattr(component, "role", None):
                        entry["role"] = component.role
                    count = getattr(component, "count", 1)
                    entry["count"] = count
                    components.append(entry)

                volumes = []
                if topology.volumes:
                    for volume in topology.volumes:
                        volumes.append({"name": str(volume.name), "type": volume.type})

                topologies_dict[str(topology.name)] = {
                    "type": str(topology.type),
                    "provider": str(topology.provider),
                    "provisioner": str(topology.provisioner),
                    "components": components,
                    "volumes": volumes,
                }

        if self.verbose:
            messages.append(f"Built topology vars: {len(topologies_dict)} topologies")

        return {"strata_topologies": topologies_dict}

    def _build_resource_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build a single dict of all resources keyed by name."""
        resources_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.resources:
            for resource in platform.spec.resources:
                resource_data: Dict[str, Any] = {
                    "type": resource.properties.resource_type,
                    "provider": resource.properties.provider_type,
                    "category": resource.properties.category,
                    "subcategory": resource.properties.subcategory,
                    "unit_cost": resource.properties.unit_cost,
                    "description": (resource.annotations.get("description", "") if resource.annotations else ""),
                    "labels": resource.labels or {},
                    "tags": resource.tags or [],
                    "count": resource.count,
                }

                if resource.configuration:
                    resource_data["configuration"] = resource.configuration
                if resource.storage:
                    resource_data["storage"] = resource.storage.model_dump(exclude_none=True)
                if resource.firewalls:
                    resource_data["firewalls"] = resource.firewalls
                if resource.firewall:
                    resource_data["firewall"] = resource.firewall
                if resource.role:
                    resource_data["role"] = resource.role

                resources_dict[str(resource.name)] = resource_data

        if self.verbose:
            messages.append(f"Built resource vars: {len(resources_dict)} resources")

        return {"strata_resources": resources_dict}

    def _build_resources_by_type(
        self,
        platform: PlatformArtifactModel,
        messages: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Build resource dicts grouped by resource_type (one file per type)."""
        by_type: Dict[str, Dict[str, Dict[str, Any]]] = {}

        if platform.spec.resources:
            for resource in platform.spec.resources:
                resource_type = (
                    resource.properties.resource_type.lower() if resource.properties.resource_type else "uncategorized"
                )

                if resource_type not in by_type:
                    by_type[resource_type] = {}

                resource_data: Dict[str, Any] = {
                    "type": resource.properties.resource_type,
                    "provider": resource.properties.provider_type,
                    "category": resource.properties.category,
                    "subcategory": resource.properties.subcategory,
                    "unit_cost": resource.properties.unit_cost,
                    "description": (resource.annotations.get("description", "") if resource.annotations else ""),
                    "labels": resource.labels or {},
                    "tags": resource.tags or [],
                    "count": resource.count,
                }

                if resource.configuration:
                    resource_data["configuration"] = resource.configuration
                if resource.storage:
                    resource_data["storage"] = resource.storage.model_dump(exclude_none=True)
                if resource.firewalls:
                    resource_data["firewalls"] = resource.firewalls
                if resource.firewall:
                    resource_data["firewall"] = resource.firewall
                if resource.role:
                    resource_data["role"] = resource.role

                by_type[resource_type][str(resource.name)] = resource_data

        if self.verbose:
            for rt, resources in by_type.items():
                messages.append(f"Built {rt} resources: {len(resources)} resources")

        return by_type

    def _build_module_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build module variable payload."""
        modules_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.modules:
            for module in platform.spec.modules:
                modules_dict[str(module.name)] = {
                    "repository": module.source.repository,
                    "source_path": module.source.source_path,
                    "target_path": module.source.target_path,
                    "description": (module.annotations.get("description", "") if module.annotations else ""),
                    "labels": module.labels or {},
                    "tags": module.tags or [],
                    "properties": (module.properties.model_dump(exclude_none=True) if module.properties else {}),
                }

        if self.verbose:
            messages.append(f"Built module vars: {len(modules_dict)} modules")

        return {"strata_modules": modules_dict}

    def _build_namespace_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build namespace variable payload."""
        namespaces_dict: Dict[str, Any] = {}

        if platform.spec.namespaces:
            for namespace in platform.spec.namespaces:
                namespaces_dict[str(namespace.name)] = {
                    "description": (namespace.annotations.get("description", "") if namespace.annotations else ""),
                    "labels": namespace.labels or {},
                    "tags": namespace.tags or [],
                    "modules": [str(m.module) for m in namespace.modules] if namespace.modules else [],
                }

        if self.verbose:
            messages.append(f"Built namespace vars: {len(namespaces_dict)} namespaces")

        return {"strata_namespaces": namespaces_dict}

    def _build_firewall_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build firewall variable payload."""
        firewalls_dict: Dict[str, Any] = {}

        if platform.spec.firewalls:
            for firewall in platform.spec.firewalls:
                rules: Dict[str, Any] = {
                    "reset": firewall.reset or False,
                    "defaults": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.defaults]
                    if firewall.defaults
                    else [],
                    "deny": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.deny]
                    if firewall.deny
                    else [],
                    "allow": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.allow]
                    if firewall.allow
                    else [],
                }
                firewalls_dict[str(firewall.name)] = {
                    "description": (firewall.annotations.get("description", "") if firewall.annotations else ""),
                    "labels": firewall.labels or {},
                    "tags": firewall.tags or [],
                    "rules": rules,
                }

        if self.verbose:
            messages.append(f"Built firewall vars: {len(firewalls_dict)} firewalls")

        return {"strata_firewalls": firewalls_dict}

    def _build_dns_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build DNS zone variable payload."""
        dns_dict: Dict[str, Any] = {}

        if platform.spec.dns_zones:
            for dns in platform.spec.dns_zones:
                zones_dict: Dict[str, Any] = {}
                for zone in dns.zones:
                    records = []
                    if zone.records:
                        for record in zone.records:
                            record_data: Dict[str, Any] = {
                                "name": record.name,
                                "type": record.type.value,
                                "ttl": record.ttl,
                                "priority": record.priority,
                            }
                            if record.value is not None:
                                record_data["value"] = record.value
                            records.append(record_data)
                    zones_dict[zone.name] = {
                        "ttl": zone.ttl,
                        "records": records,
                    }
                dns_dict[str(dns.name)] = {
                    "description": (dns.annotations.get("description", "") if dns.annotations else ""),
                    "labels": dns.labels or {},
                    "tags": dns.tags or [],
                    "provider": dns.provider,
                    "zones": zones_dict,
                }

        if self.verbose:
            messages.append(f"Built DNS vars: {len(dns_dict)} DNS zone configurations")

        return {"strata_dns_zones": dns_dict}

    def _build_network_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build network variable payload."""
        networks_dict: Dict[str, Any] = {}

        if platform.spec.networks:
            for net_attachment in platform.spec.networks:
                networks_inner: Dict[str, Any] = {}
                for network in net_attachment.networks:
                    address_space = []
                    for addr in network.address_space:
                        if addr.value is not None:
                            address_space.append(addr.value)

                    subnets_dict: Dict[str, Any] = {}
                    for subnet in network.subnets:
                        resolved_cidr = None
                        if subnet.cidr.value is not None:
                            resolved_cidr = subnet.cidr.value
                        subnets_dict[subnet.name] = {
                            "cidr": resolved_cidr,
                            "description": subnet.description,
                        }

                    peerings_dict: Dict[str, Any] = {}
                    if network.peerings:
                        for peering in network.peerings:
                            peerings_dict[peering.name] = {
                                "target": peering.target,
                            }

                    networks_inner[network.name] = {
                        "address_space": address_space,
                        "subnets": subnets_dict,
                        "peerings": peerings_dict,
                    }

                networks_dict[str(net_attachment.name)] = {
                    "description": (
                        net_attachment.annotations.get("description", "") if net_attachment.annotations else ""
                    ),
                    "labels": net_attachment.labels or {},
                    "tags": net_attachment.tags or [],
                    "networks": networks_inner,
                }

        if self.verbose:
            messages.append(f"Built network vars: {len(networks_dict)} network configurations")

        return {"strata_networks": networks_dict}

    def _build_tenant_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build tenant Ansible variable payload. Returns empty dict when no tenant is linked."""
        tenant = platform.spec.tenant
        if not tenant:
            return {}

        tenant_dict: Dict[str, Any] = {
            "code": str(tenant.code),
            "name": tenant.name,
            "zones": list(tenant.zones),
        }
        if tenant.onboarded is not None:
            tenant_dict["onboarded"] = str(tenant.onboarded)
        if tenant.configuration:
            tenant_dict["configuration"] = dict(tenant.configuration)

        if self.verbose:
            messages.append(f"Built tenant vars: {tenant.code}")

        return {"strata_tenant": tenant_dict}

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Ansible provisioner source copy
    # ------------------------------------------------------------------

    def _copy_provisioner_source(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Copy ansible source files from each provisioner's source_path into the build.

        For each ansible provisioner declared in the workspace:
          source  = repo_root / source_path   (repo_root from repo_map or work_path)
          dest    = solution_controller.get_provisioner_path(deployment_service, build_path, prov)

        In dry_run mode only logs the planned copy; no files are written.
        The generated ``strata_*.yml`` files are written on top of the copied
        source tree by ``_save_ansible_vars`` — the copy must happen first.
        """
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return True  # Nothing to copy — workspace not loaded

        provisioners = workspace_service.model.spec.provisioners or []
        deployment_build_path = deployment_service.get_build_path(build_path)
        template_context = self._build_template_context(deployment_service)

        for prov in provisioners:
            if prov.provisioner != "ansible":
                continue

            source = prov.source
            if source.source_path is None:
                self._errors.append(
                    f"Provisioner '{prov.name}' has no source_path — ansible provisioners must declare a source_path."
                )
                return False

            repo_name = str(source.repository) if source.repository else ""

            # Resolve repository root: use repo_map when available, fall back to work_path
            if repo_map and repo_name and repo_name in repo_map:
                repo_root = Path(repo_map[repo_name])
            else:
                repo_root = work_path

            src_dir = repo_root / source.source_path
            dest_dir = (
                solution_controller.get_provisioner_path(deployment_service, build_path, prov)
                if solution_controller is not None
                else deployment_build_path / (source.target_path or source.source_path)
            )

            if dry_run:
                self._messages.append(f"[DRY-RUN] Would copy ansible source: {src_dir} -> {dest_dir}")
                if not src_dir.exists():
                    self._errors.append(f"Ansible source directory not found: {src_dir} (provisioner: {prov.name})")
                    return False
                continue

            if not src_dir.exists():
                self._errors.append(f"Ansible source directory not found: {src_dir} (provisioner: {prov.name})")
                return False

            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            self._apply_templates_to_dir(dest_dir, template_context)
            self._messages.append(f"Copied ansible source: {src_dir} -> {dest_dir}")

        return True

    # Path resolution
    # ------------------------------------------------------------------

    def _resolve_ansible_paths(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        solution_controller: Optional["SolutionController"],
    ) -> List[Path]:
        """Return canonical build paths for all ansible provisioners in the workspace."""
        if solution_controller is None:
            return []
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return []
        provisioners = workspace_service.model.spec.provisioners or []
        return [
            solution_controller.get_provisioner_path(deployment_service, build_path, prov)
            for prov in provisioners
            if prov.provisioner == "ansible"
        ]

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _get_planned_files(self, ansible_vars: Dict[str, Any]) -> List[str]:
        """Return the list of filenames that would be written (non-empty only)."""
        return [name for name, _ in self._planned_file_pairs(ansible_vars)]

    def _planned_file_pairs(self, ansible_vars: Dict[str, Any]) -> List[tuple]:
        """Return ``(filename, payload)`` pairs for every non-empty Ansible variable file.

        ``workspace.yml`` is always included.  All other files are omitted when
        their data section is an empty dict, so Ansible plays that don't use a
        given feature don't receive unexpected variable files.
        """
        p = self.FILE_PREFIX
        files: List[tuple] = []

        # Always write — workspace name/labels always present
        files.append((f"{p}workspace.yml", ansible_vars["workspace"]))

        # Conditional — only when the inner data dict is non-empty
        for section_key, data_key, filename_suffix in [
            ("providers", "strata_providers", "providers.yml"),
            ("topologies", "strata_topologies", "topologies.yml"),
            ("resources", "strata_resources", "resources.yml"),
            ("modules", "strata_modules", "modules.yml"),
            ("namespaces", "strata_namespaces", "namespaces.yml"),
            ("firewalls", "strata_firewalls", "firewalls.yml"),
            ("dns", "strata_dns_zones", "dns.yml"),
            ("networks", "strata_networks", "networks.yml"),
        ]:
            payload = ansible_vars.get(section_key, {})
            if payload.get(data_key):
                files.append((f"{p}{filename_suffix}", payload))

        for resource_type, resources in ansible_vars.get("resources_by_type", {}).items():
            files.append((f"{p}resx_{resource_type}.yml", {f"strata_{resource_type}": resources}))

        if ansible_vars.get("Tenant"):
            files.append((f"{p}tenant.yml", ansible_vars["Tenant"]))

        return files

    def _save_ansible_vars(
        self,
        ansible_vars: Dict[str, Any],
        deployment_service: DeploymentService,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> List[str]:
        """Write all non-empty Ansible YAML variable files."""
        messages: List[str] = []

        try:
            ansible_paths = self._resolve_ansible_paths(deployment_service, build_path, solution_controller)
            if not ansible_paths:
                ansible_paths = [deployment_service.get_build_path(build_path) / "ansible"]

            planned = self._planned_file_pairs(ansible_vars)

            for ansible_path in ansible_paths:
                ansible_path.mkdir(parents=True, exist_ok=True)
                for filename, payload in planned:
                    self._write_yaml(ansible_path / filename, payload)
                messages.append(f"✓ Ansible artifacts saved to: {ansible_path}")

        except Exception as exc:
            error_msg = f"Failed to save Ansible artifacts: {exc}"
            self.logger.exception("Failed to save Ansible artifacts", error=str(exc))
            messages.append(error_msg)

        return messages

    def _write_yaml(self, path: Path, payload: Dict[str, Any]) -> None:
        """Write a YAML variable file with a header comment."""
        content = "---\n# Generated by strata build — do not edit manually.\n"
        content += yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True)
        path.write_text(content, encoding="utf-8")
