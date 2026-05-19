#!/usr/bin/env python3
"""Service for loading, validating, and merging firewall configurations."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from strata.exceptions import ModelValidationError, PlatformFileNotFoundError
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.configuration_model import ConfigurationModel
from strata.models.firewall_model import (
    FirewallMetaModel,
    FirewallModel,
    FirewallSpecModel,
)
from strata.services.base_service import BaseService


class FirewallService(BaseService["FirewallModel"]):
    """Service for handling firewall configurations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the FirewallService."""
        super().__init__(path=path, data=data)
        self.model = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_into(
        fw_model: FirewallModel,
        merged_allow: dict,
        merged_deny: dict,
        merged_defaults: dict,
        merged_name_holder: list,
        merged_annotations: dict,
        merged_labels: dict,
        merged_tags: list,
        reset_holder: list,
    ) -> None:
        """Accumulate one FirewallModel's data into the merge buckets."""
        if fw_model.meta.name:
            merged_name_holder[0] = fw_model.meta.name
        if fw_model.meta.annotations:
            merged_annotations.update(fw_model.meta.annotations)
        if fw_model.meta.labels:
            if isinstance(fw_model.meta.labels, dict):
                merged_labels.update(fw_model.meta.labels)
            else:
                merged_labels.update(fw_model.meta.labels.model_dump(exclude_none=True))
        if fw_model.meta.tags:
            for tag in fw_model.meta.tags:
                if tag not in merged_tags:
                    merged_tags.append(tag)

        if fw_model.spec.reset is not None:
            reset_holder[0] = fw_model.spec.reset

        if fw_model.spec.defaults:
            for default in fw_model.spec.defaults:
                merged_defaults[default.direction] = default

        def _rule_key(rule):
            return (
                rule.direction,
                rule.proto,
                str(rule.port) if rule.port else None,
                rule.from_,
                rule.to,
                rule.interface,
            )

        if fw_model.spec.allow:
            for rule in fw_model.spec.allow:
                merged_allow[_rule_key(rule)] = rule
        if fw_model.spec.deny:
            for rule in fw_model.spec.deny:
                merged_deny[_rule_key(rule)] = rule

    @staticmethod
    def _build_merged_model(
        merged_allow: dict,
        merged_deny: dict,
        merged_defaults: dict,
        merged_name: str,
        merged_annotations: dict,
        merged_labels: dict,
        merged_tags: list,
        reset: bool,
    ) -> FirewallModel:
        """Construct the final merged FirewallModel from accumulated buckets."""
        spec = FirewallSpecModel(
            reset=reset,
            defaults=list(merged_defaults.values()) or None,
            allow=list(merged_allow.values()) or None,
            deny=list(merged_deny.values()) or None,
        )
        meta = FirewallMetaModel(
            name=merged_name or "merged",
            annotations=merged_annotations or None,
            labels=merged_labels or {"version": "1.0.0"},
            tags=merged_tags or None,
        )
        return FirewallModel(apiVersion=PlatformVersion.v1, kind=PlatformKind.FIREWALL, meta=meta, spec=spec)

    # ------------------------------------------------------------------
    # Public class methods
    # ------------------------------------------------------------------

    @classmethod
    def merge_firewalls(cls, firewalls: List[Union[FirewallModel, "FirewallService"]]) -> FirewallModel:
        """
        Merge multiple firewall models or services into a single FirewallModel.

        Later entries override earlier ones for rules with the same signature
        (direction, proto, port, from_, to, interface). Defaults from the last
        entry take precedence.

        Args:
            firewalls: FirewallModel or FirewallService instances to merge.

        Returns:
            Merged FirewallModel.

        Raises:
            ValueError: If the list is empty.
            ModelValidationError: If an entry has an unexpected type.
        """
        if not firewalls:
            raise ValueError("Cannot merge empty firewall list")

        merged_allow: Dict[Any, Any] = {}
        merged_deny: Dict[Any, Any] = {}
        merged_defaults: Dict[Any, Any] = {}
        merged_name: List[str] = ["merged"]
        merged_annotations: Dict[Any, Any] = {}
        merged_labels: Dict[Any, Any] = {}
        merged_tags: List[Any] = []
        reset = [False]

        for fw in firewalls:
            if isinstance(fw, FirewallService):
                fw_model = fw.get_model()
            elif isinstance(fw, FirewallModel):
                fw_model = fw
            else:
                raise ModelValidationError(
                    model_name="FirewallService",
                    validation_errors=[
                        {
                            "field": "firewalls",
                            "message": f"Expected FirewallModel or FirewallService, got {type(fw).__name__}",
                            "type": "type_error",
                        }
                    ],
                )
            cls._merge_into(
                fw_model,
                merged_allow,
                merged_deny,
                merged_defaults,
                merged_name,
                merged_annotations,
                merged_labels,
                merged_tags,
                reset,
            )

        return cls._build_merged_model(
            merged_allow,
            merged_deny,
            merged_defaults,
            merged_name[0],
            merged_annotations,
            merged_labels,
            merged_tags,
            reset[0],
        )

    @classmethod
    def merge_fwfiles(cls, fwfiles: List[str], work_path: Path) -> FirewallModel:
        """
        Load and merge multiple firewall files into a single FirewallModel.

        Later files override earlier ones for rules with the same signature.

        Args:
            fwfiles: Firewall file paths relative to *work_path*.
            work_path: Base directory for resolving relative paths.

        Returns:
            Merged FirewallModel.

        Raises:
            PlatformFileNotFoundError: If a file does not exist.
            ModelValidationError: If a file fails validation.
        """
        merged_allow: Dict[Any, Any] = {}
        merged_deny: Dict[Any, Any] = {}
        merged_defaults: Dict[Any, Any] = {}
        merged_name: List[str] = ["merged"]
        merged_annotations: Dict[Any, Any] = {}
        merged_labels: Dict[Any, Any] = {}
        merged_tags: List[Any] = []
        reset = [False]

        for fwfile_path in fwfiles:
            full_path = work_path / fwfile_path
            if not full_path.exists():
                raise PlatformFileNotFoundError(str(full_path), file_type="Firewall")

            fw_service = cls(str(full_path))
            is_valid, errors = fw_service.validate()
            if not is_valid:
                raise ModelValidationError(
                    model_name="FirewallModel",
                    validation_errors=[
                        {"field": str(fwfile_path), "message": e, "type": "value_error"} for e in errors
                    ],
                )

            cls._merge_into(
                fw_service.get_model(),
                merged_allow,
                merged_deny,
                merged_defaults,
                merged_name,
                merged_annotations,
                merged_labels,
                merged_tags,
                reset,
            )

        return cls._build_merged_model(
            merged_allow,
            merged_deny,
            merged_defaults,
            merged_name[0],
            merged_annotations,
            merged_labels,
            merged_tags,
            reset[0],
        )

    # ------------------------------------------------------------------
    # BaseService implementation
    # ------------------------------------------------------------------

    def _get_model_class(self):
        return FirewallModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """No cross-reference validation needed — firewall rules are self-contained."""
        return True, []
