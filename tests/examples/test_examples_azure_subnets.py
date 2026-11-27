"""The reference subnet-placement module (examples/terraform/azure-subnets, docs/design/subnet-addressing.md D6).

Runs the module's offline `terraform test` suite for the pure placement logic. The Azure wrapper's tests use a
mocked azurerm provider, which needs a provider download, so they are run by hand (see the design doc).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_PLACEMENT = Path(__file__).resolve().parents[2] / "examples" / "terraform" / "azure-subnets" / "placement"
_TERRAFORM = shutil.which("terraform")


@pytest.mark.slow
@pytest.mark.skipif(_TERRAFORM is None, reason="terraform not on PATH")
def test_placement_module_terraform_tests_pass(tmp_path: Path):
    module = tmp_path / "placement"
    shutil.copytree(_PLACEMENT, module)  # init writes .terraform/ — keep it out of the repository
    terraform = str(_TERRAFORM)

    for command in ([terraform, "init", "-no-color", "-input=false"], [terraform, "test", "-no-color"]):
        result = subprocess.run(command, cwd=module, capture_output=True, text=True, timeout=600, check=False)
        assert result.returncode == 0, f"{' '.join(command[1:])} failed:\n{result.stdout}\n{result.stderr}"

    assert "0 failed" in result.stdout
