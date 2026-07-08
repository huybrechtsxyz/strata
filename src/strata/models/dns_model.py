#!/usr/bin/env python3
"""Pydantic models for DNS zone configuration validation."""

from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    VariableRefs,
    check_unique_names,
)


class DnsRecordType(str, Enum):
    """DNS record type."""

    A = "A"
    AAAA = "AAAA"
    CNAME = "CNAME"
    MX = "MX"
    TXT = "TXT"
    SRV = "SRV"
    NS = "NS"
    PTR = "PTR"
    CAA = "CAA"


class DnsReferencesModel(PlatformBaseModel):
    """References to variables and secrets required by this DNS configuration."""

    variables: VariableRefs = Field(None, description="Variable keys required from environment")
    secrets: SecretRefs = Field(None, description="Secret keys required from environment")


class DnsRecordModel(PlatformBaseModel):
    """Model for a single DNS record within a zone."""

    name: str = Field(..., min_length=1, description="Record name, e.g. '@', 'www', '_dmarc'")
    type: DnsRecordType = Field(..., description="Record type: A, AAAA, CNAME, MX, TXT, SRV, NS, PTR, CAA")
    value: Optional[str] = Field(None, description="Literal record value")
    var: Optional[str] = Field(
        None,
        description="Variable key from spec.references.variables — resolved at build time",
    )
    secret: Optional[str] = Field(
        None,
        description="Secret key from spec.references.secrets — resolved at deploy time via TF_VAR_*",
    )
    ttl: Optional[int] = Field(None, ge=1, description="Record-level TTL override in seconds (>=1 if set)")
    priority: Optional[int] = Field(None, ge=1, le=65535, description="Priority for MX/SRV records (1–65535 if set)")

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "DnsRecordModel":
        """Exactly one of value / var / secret must be set."""
        sources = [f for f in (self.value, self.var, self.secret) if f is not None]
        if len(sources) == 0:
            raise ValueError(f"DNS record '{self.name}' must have exactly one of: value, var, secret.")
        if len(sources) > 1:
            raise ValueError(
                f"DNS record '{self.name}' has multiple sources set. Use exactly one of: value, var, secret."
            )
        return self

    @model_validator(mode="after")
    def validate_priority_only_for_mx_srv(self) -> "DnsRecordModel":
        """Validate that priority is only set for MX or SRV records."""
        if self.priority is not None and self.type not in (DnsRecordType.MX, DnsRecordType.SRV):
            raise ValueError(f"'priority' is only valid for MX and SRV records, got type '{self.type.value}'")
        return self


class DnsZoneModel(PlatformBaseModel):
    """Model for a DNS zone containing one or more records."""

    name: str = Field(..., min_length=1, description="Domain name for this zone, e.g. 'huybrechts.xyz'")
    ttl: Optional[int] = Field(3600, ge=1, description="Default TTL in seconds for all records in this zone")
    records: Optional[List[DnsRecordModel]] = Field(None, description="List of DNS records in this zone")


class DnsSpecModel(PlatformBaseModel):
    """Model for a DNS configuration specification."""

    provider: Optional[str] = Field(None, description="DNS provider name, e.g. 'inwx', 'cloudflare', 'route53'")
    references: Optional[DnsReferencesModel] = Field(
        None,
        description="Variable and secret keys required by records in this DNS configuration",
    )
    zones: Annotated[
        List[DnsZoneModel],
        Field(min_length=1, description="List of DNS zones (at least one required)"),
    ]

    @model_validator(mode="after")
    def validate_unique_zone_names(self) -> "DnsSpecModel":
        """Validate that all zone names are unique."""
        if self.zones:
            check_unique_names([zone.name for zone in self.zones], "zone names")
        return self

    @model_validator(mode="after")
    def validate_references_declared(self) -> "DnsSpecModel":
        """Validate that all var/secret keys used in records are declared in references."""
        used_vars: set = set()
        used_secrets: set = set()

        if self.zones:
            for zone in self.zones:
                if zone.records:
                    for record in zone.records:
                        if record.var is not None:
                            used_vars.add(record.var)
                        if record.secret is not None:
                            used_secrets.add(record.secret)

        if not used_vars and not used_secrets:
            return self

        if self.references is None:
            keys = sorted(used_vars | used_secrets)
            raise ValueError(
                "spec.references block is required when records use var/secret sources. "
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


class DnsMetaModel(PlatformBaseModel):
    """Model for DNS resource metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(..., description="Unique name for the DNS resource.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the DNS resource.")


class DnsModel(PlatformBaseModel):
    """Top-level model for a DNS zone configuration resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version of the DNS resource model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DNS,
        frozen=True,
        description="Resource kind: always 'dns'.",
    )
    meta: DnsMetaModel = Field(..., description="Metadata for the DNS resource.")
    spec: DnsSpecModel = Field(..., description="Specification for the DNS zone configuration.")

    @model_validator(mode="after")
    def validate_kind_is_dns(self) -> "DnsModel":
        """Validate that kind is always 'dns'."""
        if self.kind != PlatformKind.DNS:
            raise ValueError(f"Expected kind 'dns', got '{self.kind}'")
        return self
