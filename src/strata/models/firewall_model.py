#!/usr/bin/env python3
"""Pydantic models for firewall ruleset validation."""

import re
from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.value_tokens import validate_cidr_or_token


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


class FirewallRuleModel(PlatformBaseModel):
    """Model for an individual firewall rule.

    Supports direction, protocol, port(s), interface, source/destination, and
    comment. ``from``/``to`` are Value bindings (ADR-0002): a literal IP or
    CIDR (e.g. ``"10.0.0.0/24"``), or a string containing ``${var:KEY}``/
    ``${secret:KEY}``/``${feature:KEY}`` tokens — this is the fix for
    ADR-0001's "Firewall Lacks Parametrization" gap (a hand-authored
    `spec.references` field was originally proposed there; superseded by
    ADR-0002's unified Value-token syntax, reusing the same mechanism already
    built for `network`/`dns`).
    """

    model_config = ConfigDict(populate_by_name=True)

    direction: FirewallDirection = Field(..., description="Direction of traffic: 'in' for inbound, 'out' for outbound.")
    proto: FirewallProtocol | None = Field(
        None, description="Protocol for the rule: 'tcp', 'udp', or 'icmp'. Optional."
    )
    port: int | str | list[int | str] | None = Field(
        None,
        description="Single port (int), port range (str like '80:90'), or list of ports/ranges. Optional.",
    )
    interface: str | None = Field(None, description="Network interface name (e.g., 'eth0', 'lo'). Optional.")
    from_: str | None = Field(
        None,
        alias="from",
        serialization_alias="from",
        description="Source IP or CIDR (for inbound rules): literal, or a string containing "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens. Optional.",
    )
    to: str | None = Field(
        None,
        description="Destination IP or CIDR (for outbound rules): literal, or a string containing "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens. Optional.",
    )
    comment: str | None = Field(None, description="Optional comment or documentation for the rule.")

    @field_validator("from_", "to")
    @classmethod
    def validate_ip_or_token(cls, v: str | None) -> str | None:
        """Validate literal IP/CIDR syntax, or Value-token syntax."""
        if v is not None:
            validate_cidr_or_token(v)
        return v

    @field_validator("interface")
    @classmethod
    def validate_interface_name(cls, v: str | None) -> str | None:
        """Validate interface name format if provided."""
        if v is not None and not re.match(r"^[a-zA-Z0-9_\-]+$", v):
            raise ValueError(f"Invalid interface name: {v}")
        return v

    @field_validator("port")
    @classmethod
    def validate_ports(cls, v: int | str | list[int | str] | None) -> int | str | list[int | str] | None:
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
    def validate_port(cls, value: int | str | list[int | str] | None) -> int | str | list[int | str] | None:
        """Validate port value: an int (1-65535), a range string ("80:90"), or a list of ports/ranges."""
        if value is None:
            return value
        if isinstance(value, int):
            if not (0 < value <= 65535):
                raise ValueError(f"Port must be between 1 and 65535: {value}")
        elif isinstance(value, list):
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


class FirewallDefaultsModel(PlatformBaseModel):
    """Model for a default firewall rule (direction, permission, comment).

    Used to set baseline allow/deny behavior for inbound/outbound traffic.
    """

    direction: FirewallDirection = Field(..., description="Direction of traffic for the default rule: 'in' or 'out'.")
    permission: FirewallPermission = Field(..., description="Permission for the default rule: 'allow' or 'deny'.")
    comment: str | None = Field(None, description="Optional comment or documentation for the default rule.")


class FirewallSpecModel(PlatformBaseModel):
    """Model for a firewall ruleset specification.

    No ``references`` field: v1 never had one for Firewall either (ADR-0001
    flagged this as a gap and proposed adding it; superseded by ADR-0002 —
    parametrization is now handled via Value tokens directly on `from`/`to`,
    not a hand-authored declared-keys list).
    """

    reset: bool | None = Field(
        False,
        description="If true, reset all existing firewall rules before applying these rules.",
    )
    defaults: list[FirewallDefaultsModel] | None = Field(
        None,
        description="List of default rules (baseline allow/deny for each direction).",
    )
    deny: list[FirewallRuleModel] | None = Field(None, description="List of explicit deny rules.")
    allow: list[FirewallRuleModel] | None = Field(None, description="List of explicit allow rules.")

    @model_validator(mode="after")
    def validate_unique_directions(self) -> "FirewallSpecModel":
        """Validate that default rules have unique directions."""
        if self.defaults:
            directions = [default.direction for default in self.defaults if default.direction]
            if len(directions) != len(set(directions)):
                raise ValueError("Default rules must have unique directions.")
        return self

    @model_validator(mode="after")
    def validate_no_conflicting_rules(self) -> "FirewallSpecModel":
        """Validate that no rule (by signature) appears in both allow and deny."""
        if self.allow and self.deny:
            allow_signatures = {
                (rule.direction, rule.proto, str(rule.port), rule.from_, rule.to) for rule in self.allow
            }
            deny_signatures = {(rule.direction, rule.proto, str(rule.port), rule.from_, rule.to) for rule in self.deny}
            conflicts = allow_signatures.intersection(deny_signatures)
            if conflicts:
                raise ValueError(f"Conflicting rules found between allow and deny: {conflicts}")
        return self


class FirewallMetaModel(PlatformBaseModel):
    """Firewall metadata including name, annotations, labels, and tags."""

    name: PlatformName = Field(..., description="Unique name for the firewall resource")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags for the firewall resource")


class FirewallModel(PlatformBaseModel):
    """Root model for a firewall ruleset configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for firewall configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.FIREWALL,
        frozen=True,
        description="Platform kind (always 'firewall')",
    )
    meta: FirewallMetaModel = Field(description="Firewall metadata (name, annotations, labels, tags)")
    spec: FirewallSpecModel = Field(description="Firewall specification (defaults, allow, deny rules)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.FIREWALL)
