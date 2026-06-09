#!/usr/bin/env python3
"""Service for loading, validating, and merging DNS zone configurations."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from strata.exceptions import ModelValidationError, PlatformFileNotFoundError
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.dns_model import (
    DnsMetaModel,
    DnsModel,
    DnsSpecModel,
    DnsZoneModel,
)
from strata.services.base_service import BaseService


class DnsService(BaseService["DnsModel"]):
    """Service for handling DNS zone configurations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the DnsService."""
        super().__init__(path=path, data=data)
        self.model = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_into(
        dns_model: DnsModel,
        merged_zones: Dict[str, Any],
        merged_name_holder: List[str],
        merged_annotations: Dict[str, Any],
        merged_labels: Dict[str, Any],
        merged_tags: List[Any],
        provider_holder: List[Optional[str]],
    ) -> None:
        """Accumulate one DnsModel's data into the merge buckets."""
        if dns_model.meta.name:
            merged_name_holder[0] = dns_model.meta.name
        if dns_model.meta.annotations:
            merged_annotations.update(dns_model.meta.annotations)
        if dns_model.meta.labels:
            merged_labels.update(dns_model.meta.labels)
        if dns_model.meta.tags:
            for tag in dns_model.meta.tags:
                if tag not in merged_tags:
                    merged_tags.append(tag)

        if dns_model.spec.provider is not None:
            provider_holder[0] = dns_model.spec.provider

        for zone in dns_model.spec.zones:
            if zone.name not in merged_zones:
                merged_zones[zone.name] = {"ttl": zone.ttl, "records": {}}
            else:
                # Last definition wins for zone-level ttl
                if zone.ttl is not None:
                    merged_zones[zone.name]["ttl"] = zone.ttl

            # Merge records by (name, type) tuple — last wins
            if zone.records:
                for record in zone.records:
                    key = (record.name, record.type)
                    merged_zones[zone.name]["records"][key] = record

    @staticmethod
    def _build_merged_model(
        merged_zones: Dict[str, Any],
        merged_name: str,
        merged_annotations: Dict[str, Any],
        merged_labels: Dict[str, Any],
        merged_tags: List[Any],
        provider: Optional[str],
    ) -> DnsModel:
        """Construct the final merged DnsModel from accumulated buckets."""
        zones = []
        for zone_name, zone_data in merged_zones.items():
            records = list(zone_data["records"].values())
            zones.append(
                DnsZoneModel(
                    name=zone_name,
                    ttl=zone_data["ttl"],
                    records=records if records else None,
                )
            )

        spec = DnsSpecModel(
            provider=provider,
            zones=zones,
        )
        meta = DnsMetaModel(
            name=merged_name or "merged",
            annotations=merged_annotations or None,
            labels=merged_labels or None,
            tags=merged_tags or None,
        )
        return DnsModel(
            apiVersion=PlatformVersion.v1,
            kind=PlatformKind.DNS,
            meta=meta,
            spec=spec,
        )

    # ------------------------------------------------------------------
    # BaseService implementation
    # ------------------------------------------------------------------

    def _get_model_class(self):
        """Return the DnsModel class."""
        return DnsModel

    def _validate_dynamic(
        self,
        configuration_model=None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """DNS has no cross-reference validation — self-contained."""
        return True, []

    # ------------------------------------------------------------------
    # Public class methods
    # ------------------------------------------------------------------

    @classmethod
    def merge_dns(cls, dns_models: List[Union[DnsModel, "DnsService"]]) -> DnsModel:
        """
        Merge multiple DNS models or services into a single DnsModel.

        Zones merge by name; records within a zone merge by (name, type) tuple.
        Later entries override earlier ones (last definition wins).

        Args:
            dns_models: DnsModel or DnsService instances to merge.

        Returns:
            Merged DnsModel.

        Raises:
            ValueError: If the list is empty.
            ModelValidationError: If an entry has an unexpected type.
        """
        if not dns_models:
            raise ValueError("Cannot merge empty DNS list")

        merged_zones: Dict[str, Any] = {}
        merged_name: List[str] = ["merged"]
        merged_annotations: Dict[str, Any] = {}
        merged_labels: Dict[str, Any] = {}
        merged_tags: List[Any] = []
        provider_holder: List[Optional[str]] = [None]

        for entry in dns_models:
            if isinstance(entry, DnsService):
                dns_model = entry.get_model()
            elif isinstance(entry, DnsModel):
                dns_model = entry
            else:
                raise ModelValidationError(
                    model_name="DnsService",
                    validation_errors=[
                        {
                            "field": "dns_models",
                            "message": f"Expected DnsModel or DnsService, got {type(entry).__name__}",
                            "type": "type_error",
                        }
                    ],
                )
            cls._merge_into(
                dns_model,
                merged_zones,
                merged_name,
                merged_annotations,
                merged_labels,
                merged_tags,
                provider_holder,
            )

        return cls._build_merged_model(
            merged_zones,
            merged_name[0],
            merged_annotations,
            merged_labels,
            merged_tags,
            provider_holder[0],
        )

    @classmethod
    def merge_dnsfiles(cls, dnsfiles: List[str], work_path: Path) -> DnsModel:
        """
        Load and merge multiple DNS configuration files into a single DnsModel.

        Later files override earlier ones for records with the same (name, type) key.

        Args:
            dnsfiles: DNS file paths relative to *work_path*.
            work_path: Base directory for resolving relative paths.

        Returns:
            Merged DnsModel.

        Raises:
            PlatformFileNotFoundError: If a file does not exist.
            ModelValidationError: If a file fails validation.
        """
        merged_zones: Dict[str, Any] = {}
        merged_name: List[str] = ["merged"]
        merged_annotations: Dict[str, Any] = {}
        merged_labels: Dict[str, Any] = {}
        merged_tags: List[Any] = []
        provider_holder: List[Optional[str]] = [None]

        for dnsfile_path in dnsfiles:
            full_path = work_path / dnsfile_path
            if not full_path.exists():
                raise PlatformFileNotFoundError(str(full_path), file_type="DNS")

            dns_service = cls(str(full_path))
            is_valid, errors = dns_service.validate()
            if not is_valid:
                raise ModelValidationError(
                    model_name="DnsModel",
                    validation_errors=[
                        {"field": str(dnsfile_path), "message": e, "type": "value_error"} for e in errors
                    ],
                )

            cls._merge_into(
                dns_service.get_model(),
                merged_zones,
                merged_name,
                merged_annotations,
                merged_labels,
                merged_tags,
                provider_holder,
            )

        return cls._build_merged_model(
            merged_zones,
            merged_name[0],
            merged_annotations,
            merged_labels,
            merged_tags,
            provider_holder[0],
        )
