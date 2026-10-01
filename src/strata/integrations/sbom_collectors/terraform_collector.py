#!/usr/bin/env python3
"""Collect Terraform provider and module components from build-output
`*.tf` files (docs/design/sbom-generation.md Phase 1) — v2 equivalent of
v1's `TerraformProviderCollector` + `TerraformModuleCollector`, merged into
one collector sharing a single HCL parse pass (the design doc's own
"Bundle the Terraform collectors" decision — same release, same file
walk, only new dependency either one would need is `python-hcl2`).
"""

from pathlib import Path
from typing import Any

import hcl2

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import terraform_module_to_purl, terraform_provider_to_purl


def _strip_hcl_string(value: Any) -> str | None:
    """Strip surrounding double-quotes that `python-hcl2` adds to string values."""
    if not isinstance(value, str):
        return str(value) if value is not None else None
    return value.strip('"')


class TerraformCollector(SbomCollector):
    """Collects Terraform provider (`required_providers {}`) and module
    (`module "x" {}`) components from every `*.tf` file under `build_path`.

    Both are deduplicated (providers by name, modules by source string;
    first occurrence wins) and parse errors on an individual file produce a
    `Severity.WARNING` diagnostic rather than aborting the collector.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []

        if not build_path.exists():
            return CollectorResult()

        providers: dict[str, dict[str, str]] = {}
        tf_modules: dict[str, str | None] = {}

        for tf_file in sorted(build_path.rglob("*.tf")):
            try:
                with tf_file.open("r", encoding="utf-8") as fh:
                    data = hcl2.load(fh)
            except Exception as exc:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.WARNING,
                        message=f"failed to parse {tf_file.name}: {exc}",
                        source="sbom:terraform",
                    )
                )
                continue

            self._extract_required_providers(data, providers)
            self._extract_modules(data, tf_modules)

        for provider_name, cfg in providers.items():
            source = cfg.get("source", provider_name)
            version = cfg.get("version")
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=provider_name,
                    version=version,
                    purl=terraform_provider_to_purl(source, version),
                    properties={},
                    source_collector="terraform",
                )
            )

        for source, version in tf_modules.items():
            purl = terraform_module_to_purl(source, version)
            if purl is None:
                continue
            base = source.split("?")[0].split("//")[0]
            if base.startswith("registry.terraform.io/"):
                base = base[len("registry.terraform.io/") :]
            parts = base.split("/")
            name = "/".join(parts[:2]) if len(parts) >= 2 else base
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=name,
                    version=version,
                    purl=purl,
                    properties={},
                    source_collector="terraform-module",
                )
            )

        return CollectorResult(components=components, diagnostics=diagnostics)

    @staticmethod
    def _extract_required_providers(data: dict[str, Any], providers: dict[str, dict[str, str]]) -> None:
        """Extract `required_providers` entries into `providers` (deduplicated, first-wins)."""
        for block in data.get("terraform") or []:
            if not isinstance(block, dict):
                continue
            for rp in block.get("required_providers") or []:
                if not isinstance(rp, dict):
                    continue
                for provider_name, cfg in rp.items():
                    if provider_name == "__is_block__" or not isinstance(cfg, dict):
                        continue
                    if provider_name in providers:
                        continue
                    source = _strip_hcl_string(cfg.get("source"))
                    version = _strip_hcl_string(cfg.get("version"))
                    entry: dict[str, str] = {}
                    if source:
                        entry["source"] = source
                    if version:
                        entry["version"] = version
                    providers[provider_name] = entry

    @staticmethod
    def _extract_modules(data: dict[str, Any], tf_modules: dict[str, str | None]) -> None:
        """Extract `module {}` block entries into `tf_modules` (deduplicated, first-wins).

        `python-hcl2` represents labeled blocks as:
            {"module": [{"label": [{"source": '"..."', "version": '"..."'}]}]}
        """
        for block in data.get("module") or []:
            if not isinstance(block, dict):
                continue
            for label, cfg_list in block.items():
                if label == "__is_block__":
                    continue
                cfgs = cfg_list if isinstance(cfg_list, list) else [cfg_list]
                for cfg in cfgs:
                    if not isinstance(cfg, dict):
                        continue
                    source = _strip_hcl_string(cfg.get("source"))
                    if not source or source in tf_modules:
                        continue
                    version = _strip_hcl_string(cfg.get("version"))
                    tf_modules[source] = version
