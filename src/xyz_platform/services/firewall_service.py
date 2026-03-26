#!/usr/bin/env python3
"""
===============================================================================
Script Name   : firewall_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Firewall service class extending BaseService for firewall configurations.
===============================================================================
"""

from pathlib import Path
from typing import List, Optional, Tuple, Union
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.firewall_model import FirewallModel
from xyz_platform.models.common_models import PlatformVersion, PlatformKind
from xyz_platform.services.base_service import BaseService


class FirewallService(BaseService):
    """Service for handling firewall configurations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the FirewallService."""
        super().__init__(path=path, data=data)
        self.model: Optional[FirewallModel] = None

    @classmethod
    def merge_firewalls(
        cls, firewalls: List[Union[FirewallModel, "FirewallService"]]
    ) -> FirewallModel:
        """
        Merge multiple firewall models or services into a single FirewallModel.

        Later firewalls override earlier ones for rules with the same signature.
        Rule signature: (direction, proto, port, from_, to, interface)
        Defaults from the last firewall take precedence.

        Args:
            firewalls: List of FirewallModel instances or FirewallService instances to merge

        Returns:
            FirewallModel: Merged firewall configuration

        Raises:
            ValueError: If input list is empty or contains invalid types
        """
        if not firewalls:
            raise ValueError("Cannot merge empty firewall list")

        merged_allow = {}
        merged_deny = {}
        merged_defaults = {}
        reset = False

        # Merge metadata - last wins for each field
        merged_name = None
        merged_annotations = {}
        merged_labels = {}
        merged_tags = []

        for fw in firewalls:
            # Extract model from service or use model directly
            if isinstance(fw, FirewallService):
                fw_model = fw.get_model()
            elif isinstance(fw, FirewallModel):
                fw_model = fw
            else:
                raise ValueError(
                    f"Invalid type in firewalls list: {type(fw)}. Expected FirewallModel or FirewallService."
                )

            # Merge metadata (last wins)
            if fw_model.meta.name:
                merged_name = fw_model.meta.name
            if fw_model.meta.annotations:
                merged_annotations.update(fw_model.meta.annotations)
            if fw_model.meta.labels:
                if isinstance(fw_model.meta.labels, dict):
                    merged_labels.update(fw_model.meta.labels)
                else:
                    # LabelsModel object
                    merged_labels.update(
                        fw_model.meta.labels.model_dump(exclude_none=True)
                    )
            if fw_model.meta.tags:
                # Tags: append unique values
                for tag in fw_model.meta.tags:
                    if tag not in merged_tags:
                        merged_tags.append(tag)

            # Reset flag: last file wins
            if fw_model.spec.reset is not None:
                reset = fw_model.spec.reset

            # Merge defaults (later ones override earlier ones by direction)
            if fw_model.spec.defaults:
                for default in fw_model.spec.defaults:
                    merged_defaults[default.direction] = default

            # Merge allow rules (later ones override earlier ones)
            if fw_model.spec.allow:
                for rule in fw_model.spec.allow:
                    # Create unique key from rule signature
                    rule_key = (
                        rule.direction,
                        rule.proto,
                        str(rule.port) if rule.port else None,
                        rule.from_,
                        rule.to,
                        rule.interface,
                    )
                    merged_allow[rule_key] = rule

            # Merge deny rules (later ones override earlier ones)
            if fw_model.spec.deny:
                for rule in fw_model.spec.deny:
                    # Create unique key from rule signature
                    rule_key = (
                        rule.direction,
                        rule.proto,
                        str(rule.port) if rule.port else None,
                        rule.from_,
                        rule.to,
                        rule.interface,
                    )
                    merged_deny[rule_key] = rule

        # Build merged FirewallModel
        from xyz_platform.models.firewall_model import (
            FirewallSpecModel,
            FirewallMetaModel,
        )

        defaults = list(merged_defaults.values()) if merged_defaults else None
        allow = list(merged_allow.values()) if merged_allow else None
        deny = list(merged_deny.values()) if merged_deny else None

        spec = FirewallSpecModel(
            reset=reset,
            defaults=defaults,
            allow=allow,
            deny=deny,
        )

        # Build merged metadata
        meta = FirewallMetaModel(
            name=merged_name if merged_name else "merged",
            annotations=merged_annotations if merged_annotations else None,
            labels=merged_labels if merged_labels else {"version": "1.0.0"},
            tags=merged_tags if merged_tags else None,
        )

        return FirewallModel(
            apiVersion=PlatformVersion.v1,
            kind=PlatformKind.FIREWALL,
            meta=meta,
            spec=spec,
        )

    @classmethod
    def merge_fwfiles(cls, fwfiles: List[str], work_path: Path) -> FirewallModel:
        """
        Merge multiple firewall files into a single FirewallModel.

        Later files override earlier files for rules with the same signature.
        Rule signature: (direction, proto, port, from_, to, interface)
        Defaults from the last file take precedence.

        Args:
            fwfiles: List of firewall file paths to merge (relative to work_path)
            work_path: Base working directory for resolving relative paths

        Returns:
            FirewallModel: Merged firewall configuration

        Raises:
            ValueError: If any firewall file is invalid
        """
        merged_allow = {}
        merged_deny = {}
        merged_defaults = {}
        reset = False

        # Merge metadata - last wins for each field
        merged_name = None
        merged_annotations = {}
        merged_labels = {}
        merged_tags = []

        for fwfile_path in fwfiles:
            fw_service = cls(str(work_path / fwfile_path))
            is_valid, errors = fw_service.validate()
            if not is_valid:
                raise ValueError(
                    f"Invalid firewall file: {fwfile_path}\nErrors: {errors}"
                )

            fw_model = fw_service.get_model()
            if fw_model is None:
                raise ValueError(f"Failed to load firewall model from: {fwfile_path}")

            # Merge metadata (last wins)
            if fw_model.meta.name:
                merged_name = fw_model.meta.name
            if fw_model.meta.annotations:
                merged_annotations.update(fw_model.meta.annotations)
            if fw_model.meta.labels:
                if isinstance(fw_model.meta.labels, dict):
                    merged_labels.update(fw_model.meta.labels)
                else:
                    # LabelsModel object
                    merged_labels.update(
                        fw_model.meta.labels.model_dump(exclude_none=True)
                    )
            if fw_model.meta.tags:
                # Tags: append unique values
                for tag in fw_model.meta.tags:
                    if tag not in merged_tags:
                        merged_tags.append(tag)

            # Reset flag: last file wins
            if fw_model.spec.reset is not None:
                reset = fw_model.spec.reset

            # Merge defaults (later files override earlier ones by direction)
            if fw_model.spec.defaults:
                for default in fw_model.spec.defaults:
                    merged_defaults[default.direction] = default

            # Merge allow rules (later files override earlier ones)
            if fw_model.spec.allow:
                for rule in fw_model.spec.allow:
                    # Create unique key from rule signature
                    rule_key = (
                        rule.direction,
                        rule.proto,
                        str(rule.port) if rule.port else None,
                        rule.from_,
                        rule.to,
                        rule.interface,
                    )
                    merged_allow[rule_key] = rule

            # Merge deny rules (later files override earlier ones)
            if fw_model.spec.deny:
                for rule in fw_model.spec.deny:
                    # Create unique key from rule signature
                    rule_key = (
                        rule.direction,
                        rule.proto,
                        str(rule.port) if rule.port else None,
                        rule.from_,
                        rule.to,
                        rule.interface,
                    )
                    merged_deny[rule_key] = rule

        # Build merged FirewallModel
        from xyz_platform.models.firewall_model import (
            FirewallSpecModel,
            FirewallMetaModel,
        )

        defaults = list(merged_defaults.values()) if merged_defaults else None
        allow = list(merged_allow.values()) if merged_allow else None
        deny = list(merged_deny.values()) if merged_deny else None

        spec = FirewallSpecModel(
            reset=reset,
            defaults=defaults,
            allow=allow,
            deny=deny,
        )

        # Build merged metadata
        meta = FirewallMetaModel(
            name=merged_name if merged_name else "merged",
            annotations=merged_annotations if merged_annotations else None,
            labels=merged_labels if merged_labels else {"version": "1.0.0"},
            tags=merged_tags if merged_tags else None,
        )

        return FirewallModel(
            apiVersion=PlatformVersion.v1,
            kind=PlatformKind.FIREWALL,
            meta=meta,
            spec=spec,
        )

    def _get_model_class(self):
        """Return the FirewallModel class for validation."""
        return FirewallModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Firewall validation is intentionally minimal since:
        - Rules are self-contained (IPs, ports, protocols, interfaces)
        - No cross-references to workspace/environment/configuration
        - No variable/secret references (uses literal values)
        - Firewalls are standalone files (no merging)

        All validation is handled by MODEL validators in FirewallSpecModel:
        - Unique default directions (one IN, one OUT)
        - No conflicting rules between allow and deny
        - Valid IP/CIDR formats, port numbers/ranges, interface names
        - Protocol-port relationship validation

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        # No cross-reference validation needed for firewall
        return True, []
