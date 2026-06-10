#!/usr/bin/env python3
"""Service for loading, validating, and merging network topology configurations."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from strata.exceptions import ModelValidationError, PlatformFileNotFoundError
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.network_model import (
    NetworkDefinitionModel,
    NetworkMetaModel,
    NetworkModel,
    NetworkReferencesModel,
    NetworkSpecModel,
)
from strata.services.base_service import BaseService


class NetworkService(BaseService["NetworkModel"]):
    """Service for handling network topology configurations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the NetworkService."""
        super().__init__(path=path, data=data)
        self.model = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_into(
        network_model: NetworkModel,
        merged_networks: Dict[str, Dict[str, Any]],
        merged_name_holder: List[str],
        merged_annotations: Dict[str, Any],
        merged_labels: Dict[str, Any],
        merged_tags: List[Any],
        merged_refs_vars: set,
        merged_refs_secrets: set,
    ) -> None:
        """Accumulate one NetworkModel's data into the merge buckets."""
        if network_model.meta.name:
            merged_name_holder[0] = network_model.meta.name
        if network_model.meta.annotations:
            merged_annotations.update(network_model.meta.annotations)
        if network_model.meta.labels:
            merged_labels.update(network_model.meta.labels)
        if network_model.meta.tags:
            for tag in network_model.meta.tags:
                if tag not in merged_tags:
                    merged_tags.append(tag)

        # Accumulate references
        if network_model.spec.references:
            if network_model.spec.references.variables:
                merged_refs_vars.update(network_model.spec.references.variables)
            if network_model.spec.references.secrets:
                merged_refs_secrets.update(network_model.spec.references.secrets)

        for network in network_model.spec.networks:
            if network.name not in merged_networks:
                merged_networks[network.name] = {
                    "description": network.description,
                    "address_space": list(network.address_space),
                    "subnets": {},
                    "peerings": {},
                }
            else:
                # Last definition wins for network-level fields
                if network.description is not None:
                    merged_networks[network.name]["description"] = network.description
                merged_networks[network.name]["address_space"] = list(network.address_space)

            # Merge subnets by (network_name, subnet_name) — last wins
            for subnet in network.subnets:
                merged_networks[network.name]["subnets"][subnet.name] = subnet

            # Merge peerings by (network_name, peering_name) — last wins
            if network.peerings:
                for peering in network.peerings:
                    merged_networks[network.name]["peerings"][peering.name] = peering

    @staticmethod
    def _build_merged_model(
        merged_networks: Dict[str, Dict[str, Any]],
        merged_name: str,
        merged_annotations: Dict[str, Any],
        merged_labels: Dict[str, Any],
        merged_tags: List[Any],
        merged_refs_vars: set,
        merged_refs_secrets: set,
    ) -> NetworkModel:
        """Construct the final merged NetworkModel from accumulated buckets."""
        networks = []
        for net_name, net_data in merged_networks.items():
            subnets = list(net_data["subnets"].values())
            peerings = list(net_data["peerings"].values()) if net_data["peerings"] else None
            networks.append(
                NetworkDefinitionModel(
                    name=net_name,
                    description=net_data["description"],
                    address_space=net_data["address_space"],
                    subnets=subnets,
                    peerings=peerings,
                )
            )

        references = None
        if merged_refs_vars or merged_refs_secrets:
            references = NetworkReferencesModel(
                variables=sorted(merged_refs_vars) if merged_refs_vars else None,
                secrets=sorted(merged_refs_secrets) if merged_refs_secrets else None,
            )

        spec = NetworkSpecModel(
            references=references,
            networks=networks,
        )
        meta = NetworkMetaModel(
            name=merged_name or "merged",
            annotations=merged_annotations or None,
            labels=merged_labels or None,
            tags=merged_tags or None,
        )
        return NetworkModel(
            apiVersion=PlatformVersion.v1,
            kind=PlatformKind.NETWORK,
            meta=meta,
            spec=spec,
        )

    # ------------------------------------------------------------------
    # BaseService implementation
    # ------------------------------------------------------------------

    def _get_model_class(self):
        """Return the NetworkModel class."""
        return NetworkModel

    def _validate_dynamic(
        self,
        configuration_model=None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Network has minimal cross-reference validation — self-contained."""
        return True, []

    # ------------------------------------------------------------------
    # Public class methods
    # ------------------------------------------------------------------

    @classmethod
    def merge_networks(cls, network_models: List[Union[NetworkModel, "NetworkService"]]) -> NetworkModel:
        """Merge multiple network models or services into a single NetworkModel.

        Networks merge by name; subnets merge by (network_name, subnet_name).
        Peerings merge by (network_name, peering_name). Later entries override
        earlier ones (last definition wins).

        Args:
            network_models: NetworkModel or NetworkService instances to merge.

        Returns:
            Merged NetworkModel.

        Raises:
            ValueError: If the list is empty.
            ModelValidationError: If an entry has an unexpected type.
        """
        if not network_models:
            raise ValueError("Cannot merge empty network list")

        merged_networks: Dict[str, Dict[str, Any]] = {}
        merged_name: List[str] = ["merged"]
        merged_annotations: Dict[str, Any] = {}
        merged_labels: Dict[str, Any] = {}
        merged_tags: List[Any] = []
        merged_refs_vars: set = set()
        merged_refs_secrets: set = set()

        for entry in network_models:
            if isinstance(entry, NetworkService):
                network_model = entry.get_model()
            elif isinstance(entry, NetworkModel):
                network_model = entry
            else:
                raise ModelValidationError(
                    model_name="NetworkService",
                    validation_errors=[
                        {
                            "field": "network_models",
                            "message": f"Expected NetworkModel or NetworkService, got {type(entry).__name__}",
                            "type": "type_error",
                        }
                    ],
                )
            cls._merge_into(
                network_model,
                merged_networks,
                merged_name,
                merged_annotations,
                merged_labels,
                merged_tags,
                merged_refs_vars,
                merged_refs_secrets,
            )

        return cls._build_merged_model(
            merged_networks,
            merged_name[0],
            merged_annotations,
            merged_labels,
            merged_tags,
            merged_refs_vars,
            merged_refs_secrets,
        )

    @classmethod
    def merge_networkfiles(cls, networkfiles: List[str], work_path: Path) -> NetworkModel:
        """Load and merge multiple network configuration files into a single NetworkModel.

        Later files override earlier ones for networks/subnets with the same name.

        Args:
            networkfiles: Network file paths relative to *work_path*.
            work_path: Base directory for resolving relative paths.

        Returns:
            Merged NetworkModel.

        Raises:
            PlatformFileNotFoundError: If a file does not exist.
            ModelValidationError: If a file fails validation.
        """
        merged_networks: Dict[str, Dict[str, Any]] = {}
        merged_name: List[str] = ["merged"]
        merged_annotations: Dict[str, Any] = {}
        merged_labels: Dict[str, Any] = {}
        merged_tags: List[Any] = []
        merged_refs_vars: set = set()
        merged_refs_secrets: set = set()

        for networkfile_path in networkfiles:
            full_path = work_path / networkfile_path
            if not full_path.exists():
                raise PlatformFileNotFoundError(str(full_path), file_type="Network")

            network_service = cls(str(full_path))
            is_valid, errors = network_service.validate()
            if not is_valid:
                raise ModelValidationError(
                    model_name="NetworkModel",
                    validation_errors=[
                        {"field": str(networkfile_path), "message": e, "type": "value_error"} for e in errors
                    ],
                )

            cls._merge_into(
                network_service.get_model(),
                merged_networks,
                merged_name,
                merged_annotations,
                merged_labels,
                merged_tags,
                merged_refs_vars,
                merged_refs_secrets,
            )

        return cls._build_merged_model(
            merged_networks,
            merged_name[0],
            merged_annotations,
            merged_labels,
            merged_tags,
            merged_refs_vars,
            merged_refs_secrets,
        )
