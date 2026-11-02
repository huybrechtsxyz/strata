#!/usr/bin/env python3
"""Pydantic model for DNS zone configuration validation."""

from enum import Enum
from typing import Any, ClassVar

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.names import check_unique_names
from strata.utils.value_tokens import has_value_tokens, validate_value_tokens


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
    """One DNS record in a zone. `value` is a literal, or contains value tokens such as `${var:KEY}`,
    `${secret:KEY}`, `${feature:KEY}`, `${output:STEP.KEY}` or `${value:kind.name.path}`, resolved at
    build or deploy time.

    `${output:STEP.KEY}` uses the output of an earlier execution step, for example to point an `A`
    record at the public IP Terraform produced. It is only accepted when this DNS document is a target
    of a provisioning step.

    Design notes:
    Model for a single DNS record within a zone.

    ``value`` is a Value binding (ADR-0002): a plain string that is either a
    literal (``"1.2.3.4"``) or contains embedded ``${var:KEY}``/
    ``${secret:KEY}``/``${feature:KEY}``/``${output:STEP.KEY}``/
    ``${value:kind.name.path}`` tokens, resolved at build/deploy time.
    ``${output:STEP.KEY}`` (docs/decisions/0036-cross-run-key-value-output-store.md)
    resolves a prior execution step's collected output — e.g. point an
    ``A`` record at whatever public IP a Terraform step ends up producing
    — as long as this record's zone is claimed by an owning step's
    ``targets`` (``docs/work/gap_fit_v1.md`` gap #12,
    ``WorkspaceService.claimed_document_names()``); an unclaimed zone still
    rejects it at ``strata validate`` time. A token's *key* (for
    ``var``/``secret``/``feature``) is checked against a real Environment
    in Phase 2.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("`priority` is only allowed on `MX` and `SRV` records.",)

    name: str = Field(..., min_length=1, description="Record name, e.g. '@', 'www', '_dmarc'")
    type: DnsRecordType = Field(..., description="Record type: A, AAAA, CNAME, MX, TXT, SRV, NS, PTR, CAA")
    value: str = Field(
        ...,
        min_length=1,
        description="Record value: a literal, or a string containing value tokens (`${var:KEY}`, "
        "`${secret:KEY}`, `${feature:KEY}`, `${output:STEP.KEY}`, "
        "`${value:kind.name.path}`).\n\n"
        "Design notes: Literal value, or a string containing "
        "'${var:KEY}'/'${secret:KEY}'/'${feature:KEY}'/'${output:STEP.KEY}'/'${value:kind.name.path}' "
        "tokens.",
    )
    ttl: int | None = Field(None, ge=1, description="Record-level TTL override in seconds (>=1 if set)")
    priority: int | None = Field(
        None,
        ge=1,
        le=65535,
        description="Priority, 1-65535. Only valid for MX and SRV records.\n\n"
        "Design notes: Priority for MX/SRV records (1-65535 if set)",
    )
    description: str | None = Field(
        None, min_length=1, description="Description of the DNS record, optionally for provider"
    )
    notes: str | None = Field(None, min_length=1, description="Additional notes for the DNS record, not for provider")
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values for your own scripts and Terraform code, passed with this "
        "record as `strata.dns.<name>.zones.<zone>.records[].custom`. Strata does not "
        "interpret them.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Rides "
        "inside this record's entry in the `dns` category payload, delivered as part "
        "of one shared `TF_VAR_dns_zones` env var — not its own env var.",
    )

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
    """One DNS zone (a domain) and its records. Each zone is its own cloud resource, so its configuration,
    custom values and tags are set here; one document can hold several zones.

    Design notes:
    Model for a DNS zone containing one or more records.

    Each zone is a real, independently-tagged cloud resource (e.g. an Azure
    DNS Zone or Route53 Hosted Zone) — configuration/custom/tags live here,
    not on `DnsSpecModel`, since a single file can declare multiple zones.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A record is a duplicate when its name, type and value are all the same. Records whose "
        "value contains a token are skipped in this comparison.",
        "A `CNAME` record's name cannot be shared with any other record in the zone, including another `CNAME`.",
    )

    name: str = Field(..., min_length=1, description="Domain name for this zone, e.g. 'huybrechts.xyz'")
    ttl: int | None = Field(3600, ge=1, description="Default TTL in seconds for all records in this zone")
    records: list[DnsRecordModel] | None = Field(None, description="List of DNS records in this zone")
    configuration: dict[str, Any] | None = Field(
        None,
        description="Free-form provisioner settings for this zone, passed to Terraform as "
        "`strata.dns.<name>.zones.<zone>.configuration`. Strata does not validate "
        "them.\n\n"
        "Design notes: Raw provisioner-specific passthrough configuration for this "
        "zone. Not validated by strata, passed through as-is to the provisioner.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values for your own scripts and Terraform code, passed to Terraform "
        "as `strata.dns.<name>.zones.<zone>.custom`. Strata does not interpret them.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Rides "
        "inside this zone's entry in the `dns` category payload, delivered as part of "
        "one shared `TF_VAR_dns_zones` env var — not its own env var.",
    )
    default_tags: dict[str, str] | None = Field(
        None,
        description="Baseline cloud tags for this zone (for example `cost-center`, `environment`, "
        "`owner`), passed to Terraform as "
        "`strata.dns.<name>.zones.<zone>.default_tags`. Not the same as `meta.tags`, "
        "which is a free-form list for documentation. Strata does not limit the number "
        "of tags, so stay within your cloud provider's limit.\n\n"
        "Design notes: Baseline cloud provider tags for this zone (e.g. cost-center, "
        "environment, owner). Deliberately distinct from meta.tags (a free-form list "
        "used for strata-internal categorization/documentation, not cloud tags). "
        "Optional (v1-derived documents commonly carry no cloud tags at all — gap #4, "
        "docs/design/gap_fit_v1.md). Strata does not enforce a maximum tag count.",
    )
    custom_tags: dict[str, str] | None = Field(
        None,
        description="Additional cloud tags on top of `default_tags`, for one-off needs.\n\n"
        "Design notes: Optional additional cloud provider tags beyond default_tags.",
    )

    @model_validator(mode="after")
    def validate_unique_records(self) -> "DnsZoneModel":
        """Reject duplicate/conflicting records within this zone (Phase 1).

        Identity is ``(name, type, value)``, not just ``(name, type)`` — a
        round-robin pair of ``A`` records, multiple ``MX``/priority entries, or
        several ``TXT`` records (SPF + DKIM) at the same name legitimately
        share ``(name, type)``. A record whose ``value`` still carries an
        unresolved Value token is skipped for the exact-duplicate comparison
        (its real value isn't known until build/deploy time) but still
        participates in the CNAME check below, which only looks at name/type.

        A ``CNAME`` may never share its name with any other record, including
        another ``CNAME`` — that's invalid in DNS itself, not just a strata
        convention (RFC 1034 §3.6.2).

        Cross-document duplication (the same zone domain declared twice, each
        individually valid) needs the whole-solution index and is a separate,
        later check — see ``_check_dns_records()`` in
        ``strata.controllers.semantic_checks``.
        """
        if not self.records:
            return self

        seen_literal: set[tuple[str, str, str]] = set()
        records_by_name: dict[str, list[DnsRecordModel]] = {}
        for record in self.records:
            if not has_value_tokens(record.value):
                literal_key = (record.name, record.type.value, record.value)
                if literal_key in seen_literal:
                    raise ValueError(
                        f"Duplicate DNS record in zone '{self.name}': name='{record.name}', "
                        f"type='{record.type.value}', value='{record.value}'"
                    )
                seen_literal.add(literal_key)
            records_by_name.setdefault(record.name, []).append(record)

        for name, records in records_by_name.items():
            if len(records) > 1 and any(record.type is DnsRecordType.CNAME for record in records):
                raise ValueError(
                    f"CNAME conflict in zone '{self.name}' for name '{name}': a CNAME record cannot "
                    "share its name with any other record, including another CNAME"
                )
        return self


class DnsSpecModel(PlatformBaseModel):
    """The DNS provider and the zones it manages.

    Design notes:
    DNS specification: provider and zones.

    No ``references`` field (ADR-0002): Value bindings (``${var:}``/``${secret:}``
    tokens in a record's ``value``) are checked against a real Environment in
    Phase 2, not an internal declared-keys list.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("Zone names must be unique within the document.",)

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
    """DNS zones and their records, managed through a DNS provider such as `inwx`, `cloudflare` or
    `route53`. A workspace uses it by listing the document in `dns_zones`.

    Design notes:
    Root model for a DNS zone configuration file.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A zone domain should be declared in only one DNS document; declaring it twice gives a warning.",
    )

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
