#!/usr/bin/env python3
"""Pydantic models for network topology configuration validation."""

import ipaddress
from typing import Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.names import check_unique_names
from strata.utils.value_tokens import has_value_tokens, validate_cidr_or_token


class SubnetModel(PlatformBaseModel):
    """Model for a subnet within a network definition.

    ``cidr`` is a Value binding (ADR-0002): a literal CIDR (e.g.
    ``"10.0.1.0/24"``) or a string containing ``${var:KEY}``/``${secret:KEY}``/
    ``${feature:KEY}`` tokens, resolved once the build/deploy layer exists.
    """

    name: PlatformName = Field(..., description="Unique subnet name within the network")
    description: str | None = Field(None, description="Optional description of the subnet")
    cidr: str = Field(
        ...,
        min_length=1,
        description="Literal CIDR (e.g. '10.0.1.0/24'), or a string containing "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens.",
    )

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        """Validate CIDR syntax (literal) or token syntax (Value binding)."""
        validate_cidr_or_token(v)
        return v


class PeeringReferenceModel(PlatformBaseModel):
    """Lightweight peering reference — name + target network only."""

    name: PlatformName = Field(..., description="Unique peering name within the network")
    target: str = Field(..., min_length=1, description="Name of the target network (must exist in same spec)")


class NetworkDefinitionModel(PlatformBaseModel):
    """Model for a single network definition with subnets and peerings.

    Each network is a real, independently-tagged cloud resource (e.g. an
    Azure VNet) — configuration/custom/tags live here, not on
    `NetworkSpecModel`, since a single file can declare multiple networks.
    """

    name: PlatformName = Field(..., description="Unique network name within the spec")
    description: str | None = Field(None, description="Optional description of the network")
    address_space: list[str] = Field(
        ...,
        min_length=1,
        description="One or more CIDRs for this network's address space (literal or "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' Value bindings)",
    )
    subnets: list[SubnetModel] = Field(..., min_length=1, description="At least one subnet required per network")
    peerings: list[PeeringReferenceModel] | None = Field(
        None, description="Optional peering references to other networks"
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Raw provisioner-specific passthrough configuration for this network. Not validated by "
        "strata, passed through as-is to the provisioner.",
    )
    custom: dict[str, Any] | None = Field(
        None, description="Custom user-defined data for scripts or extensions (e.g. becomes env vars)"
    )
    default_tags: dict[str, str] = Field(
        description="Required baseline cloud provider tags for this network (e.g. cost-center, environment, "
        "owner). Deliberately distinct from meta.tags (a free-form list used for strata-internal "
        "categorization/documentation, not cloud tags). Strata does not enforce a maximum tag count."
    )
    custom_tags: dict[str, str] | None = Field(
        None, description="Optional additional cloud provider tags beyond default_tags."
    )

    @field_validator("address_space")
    @classmethod
    def validate_address_space(cls, v: list[str]) -> list[str]:
        """Validate each address_space entry's CIDR/token syntax."""
        for cidr in v:
            validate_cidr_or_token(cidr)
        return v

    @model_validator(mode="after")
    def validate_unique_subnet_names(self) -> "NetworkDefinitionModel":
        """Validate that all subnet names are unique within this network."""
        check_unique_names([s.name for s in self.subnets], f"subnet names in network '{self.name}'")
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
            check_unique_names([p.name for p in self.peerings], f"peering names in network '{self.name}'")
        return self

    @model_validator(mode="after")
    def validate_subnet_cidr_overlap(self) -> "NetworkDefinitionModel":
        """Validate that subnet CIDRs do not overlap (only when all are literals)."""
        literal_subnets = [(s.name, s.cidr) for s in self.subnets if not has_value_tokens(s.cidr)]
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
        if any(has_value_tokens(a) for a in self.address_space):
            return self  # Skip — not all address spaces are literals

        literal_subnets = [(s.name, s.cidr) for s in self.subnets if not has_value_tokens(s.cidr)]
        if len(literal_subnets) != len(self.subnets):
            return self  # Skip — not all subnet CIDRs are literals

        supernets = [ipaddress.ip_network(a, strict=False) for a in self.address_space]
        for subnet_name, subnet_cidr in literal_subnets:
            sub = ipaddress.ip_network(subnet_cidr, strict=False)
            fits = any(
                sub.subnet_of(sup)  # type: ignore[arg-type]
                for sup in supernets
            )
            if not fits:
                raise ValueError(
                    f"Subnet '{subnet_name}' ({subnet_cidr}) in network '{self.name}' "
                    f"does not fit within any address space: {self.address_space}"
                )
        return self


class NetworkSpecModel(PlatformBaseModel):
    """Network specification: a list of network definitions.

    No ``references`` field (ADR-0002): Value bindings (``${var:}``/``${secret:}``
    tokens in `address_space`/subnet `cidr`) are checked against a real
    Environment in Phase 2, not an internal declared-keys list.
    """

    networks: list[NetworkDefinitionModel] = Field(
        ..., min_length=1, description="List of network definitions (at least one required)"
    )

    @model_validator(mode="after")
    def validate_unique_network_names(self) -> "NetworkSpecModel":
        """Validate that all network names are unique."""
        check_unique_names([n.name for n in self.networks], "network names")
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
    def validate_cross_network_cidr_overlap(self) -> "NetworkSpecModel":
        """Error for peered networks with overlapping address spaces (literals only)."""
        # Build peering set (bidirectional pairs)
        peered_pairs: set[tuple[str, str]] = set()
        for network in self.networks:
            if network.peerings:
                for peering in network.peerings:
                    a, b = sorted([network.name, peering.target])
                    peered_pairs.add((a, b))

        if not peered_pairs:
            return self

        # Collect literal address spaces per network
        network_cidrs: dict[str, list[str]] = {}
        for network in self.networks:
            if not any(has_value_tokens(a) for a in network.address_space):
                network_cidrs[network.name] = network.address_space

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
    """Network metadata including name, annotations, labels, and tags."""

    name: PlatformName = Field(..., description="Unique name for the network resource")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags for the network resource")


class NetworkModel(PlatformBaseModel):
    """Root model for a network topology configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for network configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.NETWORK,
        frozen=True,
        description="Platform kind (always 'network')",
    )
    meta: NetworkMetaModel = Field(description="Network metadata (name, annotations, labels, tags)")
    spec: NetworkSpecModel = Field(description="Network specification (network definitions)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.NETWORK)
