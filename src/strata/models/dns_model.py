#!/usr/bin/env python3
"""Pydantic model for DNS zone configuration validation."""

from enum import Enum
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
from strata.utils.value_tokens import validate_value_tokens


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


class DnsRecordModel(PlatformBaseModel):
    """Model for a single DNS record within a zone.

    ``value`` is a Value binding (ADR-0002): a plain string that is either a
    literal (``"1.2.3.4"``) or contains embedded ``${var:KEY}``/
    ``${secret:KEY}``/``${feature:KEY}`` tokens (e.g. ``"${var:public_ip}"``),
    resolved once the build/deploy layer exists. There is no ``spec.references``
    declaring which keys are valid — a token's *key* is checked against a real
    Environment in Phase 2 (deferred; Environment doesn't exist in v2 yet).

    v1 also had an ``output_key`` field (bind a record's value to a preceding
    deployment stage's provisioner output, e.g. a VM's public IP). Not ported
    for now — see [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md):
    it depends on a shared runtime "Context" store that doesn't exist in v2 yet,
    and folding it into the token syntax as ``${output:KEY}`` prematurely would
    misrepresent it as having the same validation guarantees as ``var``/``secret``/
    ``feature`` (which will be checkable against a real Environment; ``output``
    has no equivalent ground truth, in v1 or here). Re-add once Context is built.
    """

    name: str = Field(..., min_length=1, description="Record name, e.g. '@', 'www', '_dmarc'")
    type: DnsRecordType = Field(..., description="Record type: A, AAAA, CNAME, MX, TXT, SRV, NS, PTR, CAA")
    value: str = Field(
        ...,
        min_length=1,
        description="Literal value, or a string containing '${var:KEY}'/'${secret:KEY}'/'${feature:KEY}' tokens.",
    )
    ttl: int | None = Field(None, ge=1, description="Record-level TTL override in seconds (>=1 if set)")
    priority: int | None = Field(None, ge=1, le=65535, description="Priority for MX/SRV records (1-65535 if set)")
    description: str | None = Field(None, min_length=1, description="Description of the DNS record, optionally for provider")
    notes: str | None = Field(None, min_length=1, description="Additional notes for the DNS record, not for provider")

    @field_validator("value")
    @classmethod
    def validate_value_token_syntax(cls, v: str | None) -> str | None:
        """Reject malformed '${...}' tokens immediately (Phase 1).

        Whether a well-formed token's key is actually declared is a Phase 2
        check against a real Environment (deferred — see class docstring).
        """
        if v is not None:
            validate_value_tokens(v)
        return v

    @model_validator(mode="after")
    def validate_priority_only_for_mx_srv(self) -> "DnsRecordModel":
        """Validate that priority is only set for MX or SRV records."""
        if self.priority is not None and self.type not in (DnsRecordType.MX, DnsRecordType.SRV):
            raise ValueError(f"'priority' is only valid for MX and SRV records, got type '{self.type.value}'")
        return self


class DnsZoneModel(PlatformBaseModel):
    """Model for a DNS zone containing one or more records.

    Each zone is a real, independently-tagged cloud resource (e.g. an Azure
    DNS Zone or Route53 Hosted Zone) — configuration/custom/tags live here,
    not on `DnsSpecModel`, since a single file can declare multiple zones.
    """

    name: str = Field(..., min_length=1, description="Domain name for this zone, e.g. 'huybrechts.xyz'")
    ttl: int | None = Field(3600, ge=1, description="Default TTL in seconds for all records in this zone")
    records: list[DnsRecordModel] | None = Field(None, description="List of DNS records in this zone")
    configuration: dict[str, Any] | None = Field(
        None,
        description="Raw provisioner-specific passthrough configuration for this zone. Not validated by "
        "strata, passed through as-is to the provisioner.",
    )
    custom: dict[str, Any] | None = Field(
        None, description="Custom user-defined data for scripts or extensions (e.g. becomes env vars)"
    )
    default_tags: dict[str, str] = Field(
        description="Required baseline cloud provider tags for this zone (e.g. cost-center, environment, "
        "owner). Deliberately distinct from meta.tags (a free-form list used for strata-internal "
        "categorization/documentation, not cloud tags). Strata does not enforce a maximum tag count."
    )
    custom_tags: dict[str, str] | None = Field(
        None, description="Optional additional cloud provider tags beyond default_tags."
    )


class DnsSpecModel(PlatformBaseModel):
    """DNS specification: provider and zones.

    No ``references`` field (ADR-0002): Value bindings (``${var:}``/``${secret:}``
    tokens in a record's ``value``) are checked against a real Environment in
    Phase 2, not an internal declared-keys list.
    """

    provider: str | None = Field(None, description="DNS provider name, e.g. 'inwx', 'cloudflare', 'route53'")
    zones: list[DnsZoneModel] = Field(..., min_length=1, description="List of DNS zones (at least one required)")

    @model_validator(mode="after")
    def validate_unique_zone_names(self) -> "DnsSpecModel":
        """Validate that all zone names are unique."""
        if self.zones:
            check_unique_names([zone.name for zone in self.zones], "zone names")
        return self


class DnsMetaModel(PlatformBaseModel):
    """DNS metadata including name, annotations, labels, and tags."""

    name: PlatformName = Field(description="Unique name for the DNS resource")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags for the DNS resource")


class DnsModel(PlatformBaseModel):
    """Root model for a DNS zone configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for DNS configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DNS,
        frozen=True,
        description="Platform kind (always 'dns')",
    )
    meta: DnsMetaModel = Field(description="DNS metadata (name, annotations, labels, tags)")
    spec: DnsSpecModel = Field(description="DNS specification (provider, zones)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.DNS)
