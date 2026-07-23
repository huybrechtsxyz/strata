"""Command to (re)generate the SBOM from an existing platform.json."""

from pathlib import Path
from typing import Optional

import click

from strata.builders.sbom_builder import SbomBuilder
from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.services.platform_artifact_service import PlatformService


class SbomBuildCommand(BaseBuildCommand):
    """(Re)generate the SBOM from an existing ``platform.json``, or scan a directory.

    In standard mode (``-f deploy.yaml``): loads the platform artifact that was
    produced by a previous ``strata build run``, runs all SBOM collectors, and
    writes a fresh ``sbom.json`` to the same deployment build directory.

    In scan mode (``--scan PATH``): runs all file-based collectors against the
    given directory.  No deployment file, platform.json, or workspace init is
    required.  Model-dependent collectors return empty results gracefully.
    """

    OPERATION = "build_sbom"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
        report: str = "cyclonedx",
        output_file: Optional[str] = None,
        no_deps: bool = False,
        scan_path: Optional[str] = None,
        audit: bool = False,
        audit_severity: str = "MEDIUM",
        fail_on: Optional[str] = None,
        audit_report: Optional[str] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._report = report
        self._output_file: Optional[Path] = Path(output_file) if output_file else None
        self._no_deps = no_deps
        self._scan_path: Optional[Path] = Path(scan_path).resolve() if scan_path else None
        self._audit = audit
        self._audit_severity = audit_severity
        self._fail_on = fail_on
        self._audit_report = audit_report

    def get_required_integrations(self):
        return {}

    def _initialize(self, show_header: bool = True) -> bool:
        # In scan mode there is no deployment file or workspace; skip all
        # workspace init so _execute() can route directly to _execute_scan().
        if self._scan_path is not None:
            return True
        return super()._initialize(show_header=show_header)

    def _before_execute(self) -> bool:
        # In scan mode there is no deployment file — skip file validation.
        if self._scan_path is not None:
            return True
        return super()._before_execute()

    def _execute(self) -> bool:
        try:
            if self._scan_path is not None:
                return self._execute_scan()

            if self._report == "inventory":
                if not self._execute_inventory():
                    if self._is_console_output():
                        click.echo("\n❌  Inventory generation failed")
                    return False
            else:
                if not self._execute_sbom_build():
                    if self._is_console_output():
                        click.echo("\n❌  SBOM build failed")
                    return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                }
            )

            # Run CVE audit if requested (after SBOM generation)
            if self._audit and self._report != "inventory":
                sbom_path = (
                    self._deployment_service.get_build_path(self._build_path) / "sbom.json"
                    if self._deployment_service
                    else None
                )
                if sbom_path and sbom_path.exists():
                    audit_ok = self._execute_audit(sbom_path)
                    if not audit_ok:
                        return False

            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute build_sbom: {exc}")
            self.logger.exception("build_sbom failed")
            return False

    def _execute_sbom_build(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        # Load the existing platform.json to pass as pre-assembled model
        platform_path = self._deployment_service.get_build_path(self._build_path) / "platform.json"
        if not platform_path.exists():
            self._errors.append(f"Platform model not found at: {platform_path}. Run 'strata build run' first.")
            return False

        platform_service = PlatformService.load(str(platform_path), validate=True)
        if not platform_service.is_validated() or not platform_service.model:
            self._errors.append("Platform model validation failed")
            return False

        builder = SbomBuilder(verbose=self._is_verbose(), quiet=False, no_deps=self._no_deps)

        ok = builder.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=False,
            platform_model=platform_service.model,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        ok = builder.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if not ok:
            self._errors.extend(builder.get_errors())
            return False

        return True

    def _execute_inventory(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        platform_path = self._deployment_service.get_build_path(self._build_path) / "platform.json"
        if not platform_path.exists():
            self._errors.append(f"Platform model not found at: {platform_path}. Run 'strata build run' first.")
            return False

        platform_service = PlatformService.load(str(platform_path), validate=True)
        if not platform_service.is_validated() or not platform_service.model:
            self._errors.append("Platform model validation failed")
            return False

        builder = SbomBuilder(verbose=self._is_verbose(), quiet=False, no_deps=self._no_deps)
        text = builder.render_inventory(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=self._build_path,
            platform_model=platform_service.model,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(builder.drain_messages())
        if text is None:
            self._errors.extend(builder.get_errors())
            return False

        if self._output_file is not None:
            self._output_file.parent.mkdir(parents=True, exist_ok=True)
            self._output_file.write_text(text, encoding="utf-8")
            if self._is_console_output():
                click.echo(f"Inventory written to: {self._output_file}")
        else:
            click.echo(text)

        return True

    # ------------------------------------------------------------------
    # Scan mode (no deployment / workspace required)
    # ------------------------------------------------------------------

    def _execute_scan(self) -> bool:
        """Run SBOM scan against a directory without deployment context."""
        from datetime import datetime, timezone

        assert self._scan_path is not None
        self._start_time = datetime.now(timezone.utc)

        if self._is_console_output():
            self.show_console_header(work_path=str(self._scan_path))

        if not self._scan_path.is_dir():
            self._errors.append(f"Scan path is not a directory: {self._scan_path}")
            self._finalize(success=False)
            return False

        builder = SbomBuilder(verbose=self._is_verbose(), quiet=False, no_deps=self._no_deps)

        if self._report == "inventory":
            text = builder.scan_inventory(self._scan_path)
            self._messages.extend(builder.drain_messages())
            if text is None:
                self._errors.extend(builder.get_errors())
                self._finalize(success=False)
                return False

            # Emit per-component datalines for NDJSON consumers
            if self._is_ndjson_output():
                self._emit_component_datalines(builder, timezone)

            if self._output_file is not None:
                self._output_file.parent.mkdir(parents=True, exist_ok=True)
                self._output_file.write_text(text, encoding="utf-8")
                if self._is_console_output():
                    click.echo(f"Inventory written to: {self._output_file}")
            elif self._is_console_output():
                click.echo(text)

            self._output_data.update(
                {
                    "scan_path": str(self._scan_path),
                    "report": "inventory",
                    "component_count": len(builder.last_components),
                    "components": self._components_to_dicts(builder),
                }
            )
        else:
            ok = builder.scan(self._scan_path, output_file=self._output_file)
            self._messages.extend(builder.drain_messages())
            if not ok:
                self._errors.extend(builder.get_errors())
                self._finalize(success=False)
                return False

            # Emit per-component datalines for NDJSON consumers
            if self._is_ndjson_output():
                self._emit_component_datalines(builder, timezone)

            ref = builder.sbom_reference
            count = ref.component_count if ref else 0
            path = ref.path if ref else "sbom.json"
            sha256 = ref.sha256 if ref else None

            if self._is_console_output():
                click.echo(f"✅  SBOM written: {path} ({count} components)")

            self._output_data.update(
                {
                    "scan_path": str(self._scan_path),
                    "report": "cyclonedx",
                    "sbom_path": path,
                    "component_count": count,
                    "sha256": sha256,
                    "components": self._components_to_dicts(builder),
                }
            )

            # Run CVE audit if requested (scan + cyclonedx mode)
            if self._audit:
                sbom_file = Path(path) if path else self._scan_path / "sbom.json"
                if sbom_file.exists():
                    audit_ok = self._execute_audit(sbom_file)
                    if not audit_ok:
                        self._finalize(success=False)
                        return False

        self._finalize(success=True)
        return True

    def _emit_component_datalines(self, builder: SbomBuilder, timezone) -> None:
        """Emit one NDJSON ``data`` event per collected component."""
        from datetime import datetime

        for comp in builder.last_components:
            self.emit_ndjson(
                {
                    "event": "data",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "component": {
                        "name": comp.name,
                        "version": comp.version,
                        "type": comp.component_type,
                        "purl": comp.purl,
                        "collector": comp.source_collector,
                        "properties": comp.properties if comp.properties else None,
                    },
                }
            )

    @staticmethod
    def _components_to_dicts(builder: SbomBuilder) -> list:
        """Convert collected components to plain dicts for structured output."""
        return [
            {
                "name": c.name,
                "version": c.version,
                "type": c.component_type,
                "purl": c.purl,
                "collector": c.source_collector,
                "properties": c.properties if c.properties else None,
            }
            for c in builder.last_components
        ]
