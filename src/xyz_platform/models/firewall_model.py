#!/usr/bin/env python3
"""Pydantic models for firewall ruleset validation."""

import ipaddress
import re
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from xyz_platform.models.common_models import (
    PlatformKind,
    PlatformName,
    PlatformVersion,
)


class FirewallPermission(str, Enum):
    """Firewall rule permission: allow or deny."""

    ALLOW = "allow"
    DENY = "deny"


class FirewallDirection(str, Enum):
    """Firewall rule direction: inbound (in) or outbound (out)."""

    IN = "in"
    OUT = "out"


class FirewallProtocol(str, Enum):
    """Firewall rule protocol: tcp, udp, or icmp."""

    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"


class FirewallRuleModel(BaseModel):
    """
    Model for an individual firewall rule.
    Supports direction, protocol, port(s), interface, source/destination, and comments.
    Validates IP/CIDR, port formats, and interface names.
    """

    model_config = ConfigDict(populate_by_name=True)

    direction: FirewallDirection = Field(..., description="Direction of traffic: 'in' for inbound, 'out' for outbound.")
    proto: Optional[FirewallProtocol] = Field(
        None, description="Protocol for the rule: 'tcp', 'udp', or 'icmp'. Optional."
    )
    port: Optional[
        Annotated[
            Union[int, str, List[Union[int, str]]],
            Field(
                description="Single port (int), port range (str like '80:90'), or list of ports/ranges. Optional.",
            ),
        ]
    ] = None
    interface: Optional[str] = Field(None, description="Network interface name (e.g., 'eth0', 'lo'). Optional.")
    from_: Optional[str] = Field(
        None,
        alias="from",
        serialization_alias="from",
        description="Source IP address or CIDR (for inbound rules). Optional.",
    )
    to: Optional[str] = Field(
        None,
        description="Destination IP address or CIDR (for outbound rules). Optional.",
    )
    comment: Optional[str] = Field(None, description="Optional comment or documentation for the rule.")

    @field_validator("from_", "to")
    @classmethod
    def validate_ip_network(cls, v):
        if v is None:
            return v
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid IP or network: {v}") from e
        return v

    @field_validator("interface")
    @classmethod
    def validate_interface_name(cls, v):
        """Validate interface name format if provided."""
        if v is not None and not re.match(r"^[a-zA-Z0-9_\-]+$", v):
            raise ValueError(f"Invalid interface name: {v}")
        return v

    @field_validator("port")
    @classmethod
    def validate_ports(cls, v):
        return cls.validate_port(v)

    @model_validator(mode="after")
    def validate_protocol_port_relationship(self) -> "FirewallRuleModel":
        """Validate protocol and port usage."""
        if self.port is not None and self.proto is None:
            raise ValueError("Protocol (proto) must be specified when port is defined")
        if self.proto == FirewallProtocol.ICMP and self.port is not None:
            raise ValueError("ICMP protocol does not support port specifications")
        return self

    @classmethod
    def validate_port(cls, value):
        """
        Validate port value: must be an integer (1-65535), a valid range string ("80:90"), or a list of ports/ranges.
        """
        if isinstance(value, int):
            if not (0 < value <= 65535):
                raise ValueError(f"Port must be between 1 and 65535: {value}")
        elif isinstance(value, List):
            for port in value:
                cls.validate_port(port)
        elif isinstance(value, str):
            if not re.match(r"^\d{1,5}:\d{1,5}$", value):
                raise ValueError(f"Port range must be like '80:90': {value}")
            start, end = map(int, value.split(":"))
            if not (0 < start <= end <= 65535):
                raise ValueError(f"Invalid port range: {value}")
        else:
            raise ValueError(f"Invalid port value: {value}")
        return value


class FirewallDefaultsModel(BaseModel):
    """
    Model for a default firewall rule (direction, permission, comment).
    Used to set baseline allow/deny behavior for inbound/outbound traffic.
    """

    direction: FirewallDirection = Field(..., description="Direction of traffic for the default rule: 'in' or 'out'.")
    permission: FirewallPermission = Field(..., description="Permission for the default rule: 'allow' or 'deny'.")
    comment: Optional[str] = Field(None, description="Optional comment or documentation for the default rule.")


class FirewallSpecModel(BaseModel):
    """
    Model for a firewall ruleset specification.
    Includes default rules, allow/deny lists, and validation for uniqueness and conflicts.
    """

    reset: Optional[bool] = Field(
        False,
        description="If true, reset all existing firewall rules before applying these rules.",
    )
    defaults: Optional[List[FirewallDefaultsModel]] = Field(
        None,
        description="List of default rules (baseline allow/deny for each direction).",
    )
    deny: Optional[List[FirewallRuleModel]] = Field(None, description="List of explicit deny rules.")
    allow: Optional[List[FirewallRuleModel]] = Field(None, description="List of explicit allow rules.")

    # Validate direction is unique across defaults
    @model_validator(mode="after")
    def validate_unique_directions(self) -> "FirewallSpecModel":
        if self.defaults:
            directions = [default.direction for default in self.defaults if default.direction]
            if len(directions) != len(set(directions)):
                raise ValueError("Default rules must have unique directions.")
        return self

    # Validate no conflicting rules between allow and deny
    @model_validator(mode="after")
    def validate_no_conflicting_rules(self) -> "FirewallSpecModel":
        if self.allow and self.deny:
            allow_directions = {
                (rule.direction, rule.proto, str(rule.port), rule.from_, rule.to) for rule in self.allow
            }
            deny_directions = {(rule.direction, rule.proto, str(rule.port), rule.from_, rule.to) for rule in self.deny}
            conflicts = allow_directions.intersection(deny_directions)
            if conflicts:
                raise ValueError(f"Conflicting rules found between allow and deny: {conflicts}")
        return self


class FirewallMetaModel(BaseModel):
    """
    Model for firewall resource metadata (name, annotations, labels, tags).
    """

    name: PlatformName = Field(..., description="Unique name for the firewall resource.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(..., description="Labels for categorization and filtering.")
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the firewall resource.")


class FirewallModel(BaseModel):
    """
    Top-level model for a firewall ruleset resource.
    Includes metadata and specification for validation and orchestration.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version of the firewall resource model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.FIREWALL,
        frozen=True,
        description="Resource kind: always 'firewall'.",
    )
    meta: FirewallMetaModel = Field(..., description="Metadata for the firewall resource.")
    spec: FirewallSpecModel = Field(..., description="Specification for the firewall ruleset.")
