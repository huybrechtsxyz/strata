#!/usr/bin/env python3
"""Pydantic models for network topology configuration validation."""

import ipaddress
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    VariableRefs,
)


class CidrSourceModel(PlatformBaseModel):
    """CIDR value with value/var/secret union — exactly one must be set."""

    value: Optional[str] = Field(None, description="Literal CIDR notation, e.g. '10.0.0.0/24'")
    var: Optional[str] = Field(None, description="Variable key — resolved at build time")
    secret: Optional[str] = Field(None, description="Secret key — resolved at deploy time via TF_VAR_*")

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "CidrSourceModel":
        """Exactly one of value / var / secret must be set."""
        sources = [f for f in (self.value, self.var, self.secret) if f is not None]
        if len(sources) == 0:
            raise ValueError("Exactly one of value, var, or secret must be set for CIDR source.")
        if len(sources) > 1:
            raise ValueError("Multiple sources set. Use exactly one of: value, var, secret.")
        return self

    @model_validator(mode="after")
    def validate_cidr_format(self) -> "CidrSourceModel":
        """Validate that literal CIDR values are valid IP networks."""
        if self.value is not None:
            try:
                ipaddress.ip_network(self.value, strict=False)
            except ValueError as e:
                raise ValueError(f"Invalid CIDR: {self.value}") from e
        return self


class SubnetModel(PlatformBaseModel):
    """Model for a subnet within a network definition."""

    name: PlatformName = Field(..., description="Unique subnet name within the network")
    description: Optional[str] = Field(None, description="Optional description of the subnet")
    cidr: CidrSourceModel = Field(..., description="CIDR for this subnet (value/var/secret)")


class PeeringReferenceModel(PlatformBaseModel):
    """Lightweight peering reference — name + target network only."""

    name: PlatformName = Field(..., description="Unique peering name within the network")
    target: str = Field(..., min_length=1, description="Name of the target network (must exist in same spec)")


class NetworkDefinitionModel(PlatformBaseModel):
    """Model for a single network definition with subnets and peerings."""

    name: PlatformName = Field(..., description="Unique network name within the spec")
    description: Optional[str] = Field(None, description="Optional description of the network")
    address_space: List[CidrSourceModel] = Field(
        ..., min_length=1, description="One or more CIDRs for this network's address space"
    )
    subnets: List[SubnetModel] = Field(..., min_length=1, description="At least one subnet required per network")
    peerings: Optional[List[PeeringReferenceModel]] = Field(
        None, description="Optional peering references to other networks"
    )

    @model_validator(mode="after")
    def validate_unique_subnet_names(self) -> "NetworkDefinitionModel":
        """Validate that all subnet names are unique within this network."""
        subnet_names = [s.name for s in self.subnets]
        duplicates = [n for n in subnet_names if subnet_names.count(n) > 1]
        if duplicates:
            raise ValueError(f"Duplicate subnet names in network '{self.name}': {', '.join(set(duplicates))}")
        return self

    @model_validator(mode="after")
    def validate_no_self_peering(self) -> "NetworkDefinitionModel":
        """Validate that no peering targets the network itself."""
        if self.peerings:
            for peering in self.peerings:
                if peering.target == self.name:
                    raise ValueError(f"Network '{self.name}' has self-peering '{peering.name}' targeting itself")
        return self

    @model_validator(mode="after")
    def validate_unique_peering_names(self) -> "NetworkDefinitionModel":
        """Validate that all peering names are unique within this network."""
        if self.peerings:
            peering_names = [p.name for p in self.peerings]
            duplicates = [n for n in peering_names if peering_names.count(n) > 1]
            if duplicates:
                raise ValueError(f"Duplicate peering names in network '{self.name}': {', '.join(set(duplicates))}")
        return self

    @model_validator(mode="after")
    def validate_subnet_cidr_overlap(self) -> "NetworkDefinitionModel":
        """Validate that subnet CIDRs do not overlap (only when all are literals)."""
        literal_subnets = [(s.name, s.cidr.value) for s in self.subnets if s.cidr.value is not None]
        if len(literal_subnets) != len(self.subnets):
            return self  # Skip — not all CIDRs are literals

        for i, (name_a, cidr_a) in enumerate(literal_subnets):
            net_a = ipaddress.ip_network(cidr_a, strict=False)
            for name_b, cidr_b in literal_subnets[i + 1 :]:
                net_b = ipaddress.ip_network(cidr_b, strict=False)
                if net_a.overlaps(net_b):
                    raise ValueError(
                        f"Subnets overlap in network '{self.name}': "
                        f"'{name_a}' ({cidr_a}) overlaps '{name_b}' ({cidr_b})"
                    )
        return self

    @model_validator(mode="after")
    def validate_subnets_fit_address_space(self) -> "NetworkDefinitionModel":
        """Validate that all subnet CIDRs fit within the network address space (literals only)."""
        literal_address_spaces = [a.value for a in self.address_space if a.value is not None]
        if len(literal_address_spaces) != len(self.address_space):
            return self  # Skip — not all address spaces are literals

        literal_subnets = [(s.name, s.cidr.value) for s in self.subnets if s.cidr.value is not None]
        if len(literal_subnets) != len(self.subnets):
            return self  # Skip — not all subnet CIDRs are literals

        supernets = [ipaddress.ip_network(a, strict=False) for a in literal_address_spaces]
        for subnet_name, subnet_cidr in literal_subnets:
            sub = ipaddress.ip_network(subnet_cidr, strict=False)
            fits = any(
                sub.subnet_of(sup)  # type: ignore[arg-type]
                for sup in supernets
            )
            if not fits:
                raise ValueError(
                    f"Subnet '{subnet_name}' ({subnet_cidr}) in network '{self.name}' "
                    f"does not fit within any address space: {literal_address_spaces}"
                )
        return self


class NetworkReferencesModel(PlatformBaseModel):
    """References to variables and secrets required by this network configuration."""

    variables: VariableRefs = Field(None, description="Variable keys required from environment")
    secrets: SecretRefs = Field(None, description="Secret keys required from environment")


class NetworkSpecModel(PlatformBaseModel):
    """Model for a network configuration specification."""

    references: Optional[NetworkReferencesModel] = Field(
        None,
        description="Variable and secret keys required by CIDRs in this network configuration",
    )
    networks: List[NetworkDefinitionModel] = Field(
        ..., min_length=1, description="List of network definitions (at least one required)"
    )

    @model_validator(mode="after")
    def validate_unique_network_names(self) -> "NetworkSpecModel":
        """Validate that all network names are unique."""
        network_names = [n.name for n in self.networks]
        duplicates = [name for name in network_names if network_names.count(name) > 1]
        if duplicates:
            raise ValueError(f"Duplicate network names found: {', '.join(set(duplicates))}")
        return self

    @model_validator(mode="after")
    def validate_peering_targets_exist(self) -> "NetworkSpecModel":
        """Validate that all peering targets reference existing networks."""
        network_names = {n.name for n in self.networks}
        errors = []
        for network in self.networks:
            if network.peerings:
                for peering in network.peerings:
                    if peering.target not in network_names:
                        errors.append(
                            f"Network '{network.name}' peering '{peering.name}' "
                            f"targets unknown network '{peering.target}'"
                        )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_references_declared(self) -> "NetworkSpecModel":
        """Validate that all var/secret keys used in CIDRs are declared in references."""
        used_vars: set = set()
        used_secrets: set = set()

        for network in self.networks:
            for addr in network.address_space:
                if addr.var is not None:
                    used_vars.add(addr.var)
                if addr.secret is not None:
                    used_secrets.add(addr.secret)
            for subnet in network.subnets:
                if subnet.cidr.var is not None:
                    used_vars.add(subnet.cidr.var)
                if subnet.cidr.secret is not None:
                    used_secrets.add(subnet.cidr.secret)

        if not used_vars and not used_secrets:
            return self

        if self.references is None:
            keys = sorted(used_vars | used_secrets)
            raise ValueError(
                "spec.references block is required when CIDRs use var/secret sources. "
                f"Undeclared keys: {', '.join(keys)}"
            )

        declared_vars: set = set(self.references.variables or [])
        declared_secrets: set = set(self.references.secrets or [])

        undeclared_vars = used_vars - declared_vars
        undeclared_secrets = used_secrets - declared_secrets

        errors = []
        if undeclared_vars:
            errors.append(f"var keys not declared in references.variables: {', '.join(sorted(undeclared_vars))}")
        if undeclared_secrets:
            errors.append(f"secret keys not declared in references.secrets: {', '.join(sorted(undeclared_secrets))}")

        if errors:
            raise ValueError("; ".join(errors))

        return self

    @model_validator(mode="after")
    def validate_cross_network_cidr_overlap(self) -> "NetworkSpecModel":
        """Error for peered networks with overlapping address spaces (literals only)."""
        # Build peering set (bidirectional pairs)
        peered_pairs: set = set()
        for network in self.networks:
            if network.peerings:
                for peering in network.peerings:
                    pair = tuple(sorted([network.name, peering.target]))
                    peered_pairs.add(pair)

        if not peered_pairs:
            return self

        # Collect literal address spaces per network
        network_cidrs: dict = {}
        for network in self.networks:
            literals = [a.value for a in network.address_space if a.value is not None]
            if len(literals) == len(network.address_space):
                network_cidrs[network.name] = literals

        # Check peered pairs for overlap
        errors = []
        for name_a, name_b in peered_pairs:
            if name_a not in network_cidrs or name_b not in network_cidrs:
                continue  # Skip — not all CIDRs are literals
            for cidr_a in network_cidrs[name_a]:
                net_a = ipaddress.ip_network(cidr_a, strict=False)
                for cidr_b in network_cidrs[name_b]:
                    net_b = ipaddress.ip_network(cidr_b, strict=False)
                    if net_a.overlaps(net_b):
                        errors.append(
                            f"Peered networks '{name_a}' and '{name_b}' have overlapping "
                            f"address spaces: {cidr_a} overlaps {cidr_b}"
                        )

        if errors:
            raise ValueError("; ".join(errors))

        return self


class NetworkMetaModel(PlatformBaseModel):
    """Model for network resource metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(..., description="Unique name for the network resource.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the network resource.")


class NetworkModel(PlatformBaseModel):
    """Top-level model for a network topology configuration resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version of the network resource model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.NETWORK,
        frozen=True,
        description="Resource kind: always 'network'.",
    )
    meta: NetworkMetaModel = Field(..., description="Metadata for the network resource.")
    spec: NetworkSpecModel = Field(..., description="Specification for the network topology configuration.")

    @model_validator(mode="after")
    def validate_kind_is_network(self) -> "NetworkModel":
        """Validate that kind is always 'network'."""
        if self.kind != PlatformKind.NETWORK:
            raise ValueError(f"Expected kind 'network', got '{self.kind}'")
        return self
