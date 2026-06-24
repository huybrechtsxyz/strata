"""Command to build and render workspace dependency graphs."""

from pathlib import Path
from typing import Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.graph_controller import GraphController
from strata.utils.graph import (
    GraphResult,
    compute_deployment_order,
    render_mermaid,
    render_mermaid_live_url,
    render_mermaid_resources,
    render_tree,
)


class GraphCommand(BaseCommand):
    """Build and render a workspace dependency graph (file or resource mode)."""

    OPERATION = "graph"
    INIT_REQUIRED = False

    def __init__(
        self,
        mode: str = "files",
        entry: Optional[str] = None,
        save: Optional[str] = None,
        direction: Optional[str] = None,
        no_validate: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._mode = mode
        self._entry = entry
        self._save = save
        self._direction = direction or ("TD" if mode == "resources" else "LR")
        self._no_validate = no_validate
        self._result: Optional[GraphResult] = None

    def get_required_integrations(self) -> dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        """Return True if the graph contains missing/invalid/dangling nodes."""
        if not self._result:
            return False
        problem_statuses = {"missing", "invalid", "dangling"}
        return any(n.status in problem_statuses for n in self._result.nodes)

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self._finalize(success=False)
                return False

            controller = GraphController(
                work_path=self._work_path,
                entry=self._entry,
                no_validate=self._no_validate,
            )

            if self._mode == "resources":
                self._result = controller.build_resource_graph()
            else:
                self._result = controller.build_file_graph()

            if controller.has_errors():
                for err in controller.get_errors():
                    self._errors.append(err)
                self._finalize(success=False)
                return False

            # Render output
            self._render_output(self._result)

            self._finalize(success=True)
            return True

        except Exception as e:
            self.logger.exception("Failed to build graph", error=str(e))
            self._errors.append(f"Failed to build graph: {e}")
            self._finalize(success=False)
            return False

    def _render_output(self, result: GraphResult) -> None:
        """Render the graph based on output format."""
        if self._output_format == "json":
            self._render_json(result)
        else:
            self._render_console(result)

        # Save to file if requested
        if self._save:
            self._save_mermaid_file(result)

    def _render_console(self, result: GraphResult) -> None:
        """Render text tree to console."""
        if self._output_quiet:
            return
        tree = render_tree(result)
        click.echo(tree)

        if self._output_verbose and result.mode == "resources":
            layers = compute_deployment_order(result)
            if layers:
                click.echo("\nDeployment Order (topological):")
                for i, layer in enumerate(layers, 1):
                    click.echo(f"  {i}. {', '.join(layer)}")

    def _render_json(self, result: GraphResult) -> None:
        """Render JSON output."""
        import json

        data: dict = {
            "success": True,
            "data": {
                "mode": result.mode,
                "entry_points": result.entry_points,
                "nodes": [
                    {
                        "identifier": n.identifier,
                        "path": n.path,
                        "name": n.name,
                        "kind": n.kind,
                        "status": n.status,
                        "errors": n.errors if n.errors else None,
                    }
                    for n in result.nodes
                ],
                "edges": [{"source": e.source, "target": e.target, "label": e.label} for e in result.edges],
                "summary": self._build_summary(result),
            },
        }
        if result.mode == "resources" and result.topologies:
            data["data"]["topologies"] = [
                {
                    "name": t.name,
                    "provisioner": t.provisioner,
                    "provider": t.provider,
                    "type": t.type,
                    "components": t.components,
                    "namespaces": t.namespaces,
                }
                for t in result.topologies
            ]
        click.echo(json.dumps(data, indent=2))

    def _save_mermaid_file(self, result: GraphResult) -> None:
        """Save Mermaid markdown to a file."""
        save_path = Path(self._save) if self._save else self._work_path / "graph.md"
        if not save_path.is_absolute():
            save_path = self._work_path / save_path

        # Render Mermaid
        if result.mode == "resources":
            mermaid = render_mermaid_resources(result, self._direction)
        else:
            mermaid = render_mermaid(result, self._direction)

        # Build markdown content
        lines = [
            "# Workspace Dependency Graph",
            "",
            f"Mode: {result.mode}",
            f"Entry points: {', '.join(result.entry_points)}",
            "",
            "## Graph",
            "",
            "```text",
            mermaid,
            "```",
            "",
            "## Summary",
            "",
        ]

        summary = self._build_summary(result)
        for key, value in summary.items():
            lines.append(f"- **{key}:** {value}")

        lines.append("")
        lines.append("## Mermaid Live")
        lines.append("")
        lines.append(f"[Open in Mermaid Live Editor]({render_mermaid_live_url(mermaid)})")
        lines.append("")

        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text("\n".join(lines), encoding="utf-8")
        if not self._output_quiet:
            click.echo(f"\n📄 Saved to: {save_path}")

    def _build_summary(self, result: GraphResult) -> dict:
        """Build summary statistics."""
        if result.mode == "resources":
            return {
                "resources": sum(1 for n in result.nodes if n.kind == "resource"),
                "modules": sum(1 for n in result.nodes if n.kind == "module"),
                "namespaces": sum(1 for n in result.nodes if n.kind == "namespace"),
                "networks": sum(1 for n in result.nodes if n.kind == "network"),
                "dependencies": sum(1 for e in result.edges if e.label == "depends_on"),
            }
        else:
            status_counts: dict[str, int] = {}
            for node in result.nodes:
                status_counts[node.status] = status_counts.get(node.status, 0) + 1
            return status_counts
