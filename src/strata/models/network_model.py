#!/usr/bin/env python3
"""Pydantic models for network topology configuration validation."""

import ipaddress
from collections.abc import Iterable
from typing import Any, ClassVar, cast

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.names import check_unique_names
from strata.utils.value_tokens import has_value_tokens, validate_cidr_or_token, validate_value_tokens


class SubnetModel(PlatformBaseModel):
    """A subnet of a network: a `name` and either a fixed `cidr` (a literal CIDR such as `10.0.1.0/24`, or a
    string with value tokens such as `${var:KEY}`) or a `prefix_length`, when your provisioner code or an IPAM
    chooses the address at deploy.

    Design notes:
    Model for a subnet within a network definition.

    ``cidr`` is a Value binding (ADR-0002): a literal CIDR (e.g.
    ``"10.0.1.0/24"``) or a string containing ``${var:KEY}``/``${secret:KEY}``/
    ``${feature:KEY}`` tokens, resolved once the build/deploy layer exists.

    ``prefix_length`` (docs/design/subnet-addressing.md, D1): strata
    declares the size, never computes the address — the consuming Terraform
    (reading the existing vnet as the register) or an IPAM allocates it.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("A subnet gives exactly one of `cidr` and `prefix_length`.",)

    name: PlatformName = Field(..., description="Unique subnet name within the network")
    description: str | None = Field(None, description="Optional description of the subnet")
    cidr: str | None = Field(
        None,
        min_length=1,
        description="A fixed address: a literal CIDR (e.g. '10.0.1.0/24'), or a string containing "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens. Give either `cidr` or `prefix_length`.",
    )
    prefix_length: int | None = Field(
        None,
        ge=1,
        le=128,
        description="The subnet's size as a prefix length (`27` is a /27), when the address is not written "
        "here but chosen at deploy by your provisioner code or an IPAM. Strata passes it on and never "
        "computes the address. Give either `cidr` or `prefix_length`.\n\n"
        "Design notes: Approach 5 of docs/design/subnet-addressing.md: strata declares WHERE/HOW, "
        "Terraform or an IPAM allocates. Named prefix_length rather than size, which reads as an address count.",
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Free-form provisioner settings for this subnet (for example delegations, "
        "service endpoints or a security group association), passed to Terraform as "
        "`strata.networks.<name>.networks.<network>.subnets.<subnet>.configuration`. "
        "Strata does not validate them.\n\n"
        "Design notes: Raw provisioner-specific passthrough configuration for this "
        "subnet (e.g. delegations, service endpoints, NSG association). Not validated "
        "by strata, passed through as-is to the provisioner.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values for your own scripts and Terraform code, passed to Terraform "
        "as `strata.networks.<name>.networks.<network>.subnets.<subnet>.custom`. "
        "Strata does not interpret them.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Rides "
        "inside this subnet's entry in the `networks` category payload, delivered as "
        "part of one shared `TF_VAR_networks` env var — not its own env var.",
    )

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str | None) -> str | None:
        """Validate CIDR syntax (literal) or token syntax (Value binding)."""
        if v is not None:
            validate_cidr_or_token(v)
        return v

    @model_validator(mode="after")
    def validate_subnet_address(self) -> "SubnetModel":
        """A subnet is either fixed (`cidr`) or sized (`prefix_length`), never both or neither."""
        if (self.cidr is None) == (self.prefix_length is None):
            raise ValueError(f"Subnet '{self.name}' needs exactly one of 'cidr' or 'prefix_length'")
        return self


class NetworkIpamModel(PlatformBaseModel):
    """Which IPAM allocates this network's addresses. Strata does not call the IPAM: it passes these settings on
    to your provisioner code, which does.

    Design notes:
    docs/design/subnet-addressing.md, D2: strata integrates with no IPAM —
    no API calls, no IPAM-specific rules. `type` is a free name, not an enum,
    because strata never branches on it.
    """

    type: PlatformName = Field(
        ...,
        description="Which IPAM, for example `azure-avnm`, `netbox`, `infoblox` or `phpipam`. A free name your "
        "provisioner code recognises; strata does not interpret it.",
    )
    pool: str | None = Field(
        None,
        min_length=1,
        description="The IPAM pool or prefix to allocate from, for example a pool resource id. May contain "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens, since the pool usually differs per environment.",
    )
    prefix_length: int | None = Field(
        None,
        ge=1,
        le=128,
        description="The size the network asks the IPAM for, as a prefix length (`22` is a /22). Without a "
        "literal `address_space`, the network's subnets are checked against it: none larger, and not more "
        "addresses in total than it holds.",
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Free-form IPAM settings for your provisioner code, passed through as-is. Strata does not "
        "validate them.",
    )

    @field_validator("pool")
    @classmethod
    def validate_pool(cls, v: str | None) -> str | None:
        """Validate the token syntax of the pool (a Value binding)."""
        if v is not None:
            validate_value_tokens(v)
        return v


class PeeringReferenceModel(PlatformBaseModel):
    """Lightweight peering reference — name + target network only."""

    name: PlatformName = Field(..., description="Unique peering name within the network")
    target: str = Field(..., min_length=1, description="Name of the target network (must exist in same spec)")


class NetworkDefinitionModel(PlatformBaseModel):
    """One virtual network: its address space, subnets and optional peerings. Each network is its own cloud
    resource, so its configuration, custom values and tags are set here; one document can hold several
    networks.

    Design notes:
    Model for a single network definition with subnets and peerings.

    Each network is a real, independently-tagged cloud resource (e.g. an
    Azure VNet) — configuration/custom/tags live here, not on
    `NetworkSpecModel`, since a single file can declare multiple networks.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A network gives `address_space`, `ipam`, or both.",
        "Subnet names must be unique within the network.",
        "Peering names must be unique within the network, and a network cannot peer with itself.",
        "Subnets with a `cidr` must not overlap each other and must fit inside the network's address space. "
        "This is only checked when all the CIDRs involved are literals rather than tokens.",
        "A subnet's `prefix_length` must fit inside at least one address space range: not shorter than the "
        "range's own prefix, and valid for its IP version. This is only checked when the address space is "
        "literal; without one, a subnet must not be larger than the `ipam.prefix_length` block.",
        "All subnets together, fixed and sized, must not need more addresses than the address space holds. "
        "This is only checked when the address space is literal and of one IP version, or, without one, "
        "against the `ipam.prefix_length` block. A fixed subnet that is still a token is not counted.",
    )

    name: PlatformName = Field(..., description="Unique network name within the spec")
    description: str | None = Field(None, description="Optional description of the network")
    address_space: list[str] | None = Field(
        None,
        min_length=1,
        description="One or more CIDRs for this network's address space (literal or "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' Value bindings). Optional when `ipam` is given.",
    )
    ipam: NetworkIpamModel | None = Field(
        None,
        description="Which IPAM allocates this network's addresses, passed on to your provisioner code. Strata "
        "does not call the IPAM itself.",
    )
    subnets: list[SubnetModel] = Field(
        default_factory=list,
        description="The network's subnets. May be empty when every subnet is declared by the workloads that use "
        "the network.",
    )
    peerings: list[PeeringReferenceModel] | None = Field(
        None, description="Optional peering references to other networks"
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Free-form provisioner settings for this network, passed to Terraform as "
        "`strata.networks.<name>.networks.<network>.configuration`. Strata does not "
        "validate them.\n\n"
        "Design notes: Raw provisioner-specific passthrough configuration for this "
        "network. Not validated by strata, passed through as-is to the provisioner.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values for your own scripts and Terraform code, passed to Terraform "
        "as `strata.networks.<name>.networks.<network>.custom`. Strata does not "
        "interpret them.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Rides "
        "inside this network's entry in the `networks` category payload, delivered as "
        "part of one shared `TF_VAR_networks` env var — not its own env var.",
    )
    default_tags: dict[str, str] | None = Field(
        None,
        description="Baseline cloud tags for this network (for example `cost-center`, "
        "`environment`, `owner`), passed to Terraform as "
        "`strata.networks.<name>.networks.<network>.default_tags`. Not the same as "
        "`meta.tags`, which is a free-form list for documentation. Strata does not "
        "limit the number of tags, so stay within your cloud provider's limit.\n\n"
        "Design notes: Baseline cloud provider tags for this network (e.g. "
        "cost-center, environment, owner). Deliberately distinct from meta.tags (a "
        "free-form list used for strata-internal categorization/documentation, not "
        "cloud tags). Optional (v1-derived documents commonly carry no cloud tags at "
        "all — gap #4, docs/work/gap_fit_v1.md). Strata does not enforce a maximum "
        "tag count.",
    )
    custom_tags: dict[str, str] | None = Field(
        None,
        description="Additional cloud tags on top of `default_tags`, for one-off needs.\n\n"
        "Design notes: Optional additional cloud provider tags beyond default_tags.",
    )

    @field_validator("address_space")
    @classmethod
    def validate_address_space(cls, v: list[str] | None) -> list[str] | None:
        """Validate each address_space entry's CIDR/token syntax."""
        for cidr in v or []:
            validate_cidr_or_token(cidr)
        return v

    @model_validator(mode="after")
    def validate_address_space_or_ipam(self) -> "NetworkDefinitionModel":
        """A network says where its addresses come from: its own ranges, an IPAM, or both."""
        if self.address_space is None and self.ipam is None:
            raise ValueError(f"Network '{self.name}' needs 'address_space', 'ipam', or both")
        return self

    def literal_ranges(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None:
        """The address space as networks, or `None` when it is absent or any range is still a token."""
        if not self.address_space or any(has_value_tokens(a) for a in self.address_space):
            return None
        return [ipaddress.ip_network(a, strict=False) for a in self.address_space]

    def ipam_block(self) -> int | None:
        """The prefix length of the block the network asks its IPAM for, when that is the space to check.

        Only when there are no literal ranges: a literal `address_space` is what the IPAM manages, known up
        front, and stays the space the checks use (D2). `None` when the IPAM gives no `prefix_length`.
        """
        if self.literal_ranges() is not None or self.ipam is None:
            return None
        return self.ipam.prefix_length

    def space_description(self) -> str:
        """How a message names the space the subnets must fit: the address space, or the IPAM block."""
        block = self.ipam_block()
        if block is not None:
            return f"the /{block} block it asks the IPAM for"
        return f"its address space {self.address_space}"

    def prefix_length_fits(self, prefix_length: int) -> bool | None:
        """Whether a `/prefix_length` block can fit one of the ranges: not larger, same IP version.

        Without literal ranges, the block the network asks its IPAM for is the range; its IP version is not
        known, so only "not larger" is checked. `None` when it cannot be checked at all. Shared by the
        document's own sized subnets and a workspace's, so both are judged by the same rule.
        """
        ranges = self.literal_ranges()
        if ranges is not None:
            return any(r.prefixlen <= prefix_length <= r.max_prefixlen for r in ranges)
        block = self.ipam_block()
        return None if block is None else prefix_length >= block

    def address_shortfall(self, extra_prefix_lengths: Iterable[int] = ()) -> tuple[int, int] | None:
        """`(needed, available)` when the subnets certainly need more addresses than the ranges hold, else `None`.

        Counts every literal fixed subnet's addresses, every sized subnet's `2^(bits - prefix_length)`, and
        `extra_prefix_lengths` (subnets a workspace adds). A fixed subnet that is still a token is left out,
        which keeps the count a lower bound. Without literal ranges, the block the network asks its IPAM for
        is the space (`ipam_block()`). `None` too when it cannot be checked: neither of those, or mixed IP
        versions. A lower bound only — alignment, and subnets other workspaces add, can still make an
        allocation fail at deploy.
        """
        fixed = [
            ipaddress.ip_network(s.cidr, strict=False)
            for s in self.subnets
            if s.cidr is not None and not has_value_tokens(s.cidr)
        ]
        lengths = [s.prefix_length for s in self.subnets if s.prefix_length is not None] + list(extra_prefix_lengths)

        ranges = self.literal_ranges()
        if ranges is not None:
            if len({r.version for r in ranges}) != 1:
                return None
            bits = ranges[0].max_prefixlen
            collapsed = ipaddress.collapse_addresses(cast(list[ipaddress.IPv4Network], ranges))
            available = sum(network.num_addresses for network in collapsed)
        else:
            block = self.ipam_block()
            versions = {f.version for f in fixed}
            if block is None or len(versions) > 1:
                return None
            # The IP version is not given; a fixed subnet shows it, else any length past /32 means IPv6. The
            # verdict does not depend on it — only the numbers in the message do.
            ipv6 = versions == {6} or (not versions and max([block, *lengths]) > 32)
            bits = 128 if ipv6 else 32
            if block > bits:
                return None
            available = 2 ** (bits - block)

        needed = sum(f.num_addresses for f in fixed)
        needed += sum(2 ** (bits - length) for length in lengths if length <= bits)
        return (needed, available) if needed > available else None

    def _literal_fixed_subnets(self) -> list[tuple[str, str]] | None:
        """`(name, cidr)` of every subnet with a `cidr`, or `None` when any of those is still a token.

        Subnets with a `prefix_length` have no address yet and take no part in the overlap/fit checks.
        """
        fixed = [(s.name, s.cidr) for s in self.subnets if s.cidr is not None]
        if any(has_value_tokens(cidr) for _, cidr in fixed):
            return None
        return fixed

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
        literal_subnets = self._literal_fixed_subnets()
        if literal_subnets is None:
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
        supernets = self.literal_ranges()
        if supernets is None:
            return self  # Skip — no address space, or not all of it is literal

        literal_subnets = self._literal_fixed_subnets()
        if literal_subnets is None:
            return self  # Skip — not all subnet CIDRs are literals

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

    @model_validator(mode="after")
    def validate_prefix_lengths_fit_address_space(self) -> "NetworkDefinitionModel":
        """A sized subnet must fit at least one literal range (not larger than it, same IP version), or else
        the block the network asks its IPAM for.

        Only a "certainly cannot fit" check — whether a free block is still left is decided at deploy by
        the provisioner code or the IPAM, which see the subnets that really exist.
        """
        for subnet in self.subnets:
            length = subnet.prefix_length
            if length is None:
                continue
            if self.prefix_length_fits(length) is False:  # None: nothing to check against
                raise ValueError(f"Subnet '{subnet.name}' (/{length}) in network '{self.name}' {self.misfit_text()}")
        return self

    def misfit_text(self) -> str:
        """The end of a "subnet does not fit" message: against the address space, or the IPAM block."""
        block = self.ipam_block()
        if block is not None:
            return f"is larger than the /{block} block it asks the IPAM for"
        return f"does not fit within any address space: {self.address_space}"

    @model_validator(mode="after")
    def validate_subnets_fit_capacity(self) -> "NetworkDefinitionModel":
        """The subnets together must not need more addresses than the literal ranges, or the IPAM block, hold."""
        shortfall = self.address_shortfall()
        if shortfall is not None:
            needed, available = shortfall
            raise ValueError(
                f"Subnets of network '{self.name}' need {needed} addresses, but {self.space_description()} "
                f"holds only {available}"
            )
        return self


class NetworkSpecModel(PlatformBaseModel):
    """The networks declared by this document.

    Design notes:
    Network specification: a list of network definitions.

    No ``references`` field (ADR-0002): Value bindings (``${var:}``/``${secret:}``
    tokens in `address_space`/subnet `cidr`) are checked against a real
    Environment in Phase 2, not an internal declared-keys list.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "Network names must be unique within the document.",
        "Every peering `target` must name a network in the same document.",
        "Peered networks must not have overlapping address spaces. This is only checked for literal CIDRs, "
        "and not for a network without `address_space`.",
    )

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
            if network.address_space and not any(has_value_tokens(a) for a in network.address_space):
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
    """Virtual networks with their address spaces, subnets and peerings. A workspace uses it by listing the
    document in `networks`, and a workspace resource can be placed on one of its subnets.

    Design notes:
    Root model for a network topology configuration file.
    """

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

    def target_definition(self, virtual_network: str | None) -> NetworkDefinitionModel | None:
        """The network a workspace subnet goes into: the one named, or the document's only one.

        `None` when the name is unknown, or when none is named and the document declares several. The one
        rule for both validation (which reports it) and the payload (which then leaves the subnet out).
        """
        if virtual_network is None:
            return self.spec.networks[0] if len(self.spec.networks) == 1 else None
        return next((net for net in self.spec.networks if net.name == virtual_network), None)
