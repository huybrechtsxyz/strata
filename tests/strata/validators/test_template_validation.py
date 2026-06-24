"""CI template validation — ensures built-in scaffold templates produce valid YAML.

Runs `sln init --template <name>` for each built-in scaffold template, then
validates every generated platform YAML file with PlatformValidator.

Prevents schema drift (e.g., extra fields that violate ``extra="forbid"``).
See ADR 0014, item #4.
"""

from pathlib import Path

import pytest

from strata.commands.init.init_solution_command import InitSolutionCommand
from strata.validators.platform_validator import PlatformValidator

# ---------------------------------------------------------------------------
# Discover built-in templates
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "src" / "strata" / "templates" / "examples"


def _builtin_template_names() -> list[str]:
    """Return names of all built-in scaffold templates that have a scaffold/ subdirectory."""
    if not _EXAMPLES_DIR.is_dir():
        return []
    return sorted(d.name for d in _EXAMPLES_DIR.iterdir() if d.is_dir() and (d / "scaffold").is_dir())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuiltinTemplateValidation:
    """Each built-in template must produce YAML files that pass Pydantic validation."""

    @pytest.fixture(params=_builtin_template_names(), ids=_builtin_template_names())
    def scaffolded_workspace(self, request, tmp_path: Path) -> Path:
        """Run sln init --template <name> and return the workspace path."""
        template_name = request.param
        command = InitSolutionCommand(
            name="testapp",
            template=template_name,
            work_path=str(tmp_path),
            quiet=True,
        )
        result = command.execute()
        assert result, f"sln init --template {template_name} failed: {command._errors}"
        return tmp_path

    def test_all_yaml_files_validate(self, scaffolded_workspace: Path):
        """Every .yaml file in the scaffolded workspace must pass PlatformValidator."""
        yaml_files = [
            f
            for f in scaffolded_workspace.rglob("*.yaml")
            if f.is_file()
            # Skip non-platform files (e.g., .strata/cli.yaml, VS Code settings)
            and ".strata" not in f.parts
            and ".vscode" not in f.parts
        ]

        assert yaml_files, "Expected at least one YAML file in the scaffolded workspace"

        failures: list[str] = []
        for yaml_file in sorted(yaml_files):
            validator = PlatformValidator(file_path=yaml_file)
            if not validator.before_validate(work_path=scaffolded_workspace):
                errors = validator.get_errors()
                failures.append(f"  {yaml_file.relative_to(scaffolded_workspace)}: {errors}")
                continue
            if not validator.validate(work_path=scaffolded_workspace):
                errors = validator.get_errors()
                failures.append(f"  {yaml_file.relative_to(scaffolded_workspace)}: {errors}")

        if failures:
            msg = "Template validation failures:\n" + "\n".join(failures)
            pytest.fail(msg)
