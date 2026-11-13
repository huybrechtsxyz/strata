#!/usr/bin/env python3
"""The how-to `docs/how-to/parameterised-deployment-bases.md` builds a solution that validates.

Every ```yaml block of the page whose first line is `# <path>` is a file of that solution. The test writes them all
and loads the solution, so the page cannot show configuration strata would reject.
"""

import re
from pathlib import Path

import pytest

from strata.controllers.deployment_resolution import resolve_deployments
from strata.controllers.rendered_view import render_document
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind

HOWTO = Path(__file__).resolve().parents[2] / "docs" / "how-to" / "parameterised-deployment-bases.md"
_BLOCK = re.compile(r"^```yaml\n# (?P<path>\S+\.yaml)\n(?P<body>.*?)^```$", re.M | re.S)


def _files() -> dict[str, str]:
    return {match["path"]: match["body"] for match in _BLOCK.finditer(HOWTO.read_text(encoding="utf-8"))}


@pytest.fixture
def solution(tmp_path: Path) -> Path:
    for relative, body in _files().items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def test_the_page_shows_every_file_of_its_layout():
    files = set(_files())
    assert len(files) == 20
    layout = HOWTO.read_text(encoding="utf-8").split("## The layout", 1)[1].split("```", 2)[1]
    for line in layout.splitlines():
        name = line.split()[0] if line.strip() else ""
        if name.endswith(".yaml"):
            assert any(path.endswith(name) for path in files), name


def test_the_solution_validates_strictly_with_nothing_unused(solution):
    context = open_solution(solution).require_valid()
    assert not context.diagnostics.warnings  # `strata validate --strict` passes
    assert [f for f in context.diagnostics.infos if f.code == "param_unused"] == []


def test_the_rendered_origins_are_as_the_page_shows(solution):
    context = open_solution(solution)
    rendered, _ = render_document(context.controller.index, PlatformKind.DEPLOYMENT, "spoke-initech-001")
    assert rendered is not None
    region = next(origin for origin in rendered.fields if origin.path == "spec.properties.region")
    assert (region.value, region.document, region.written) == (
        "westus2",
        "spoke_deployment_base",
        "${param:hub.region}",
    )
    assert [(read.role, read.key, read.parameter_set) for read in region.reads] == [("hub", "region", "hub-hub02")]
    assert rendered.param_sets == {"hub": ("hub-defaults", "hub-hub02"), "spoke": ("spoke-initech-001",)}


def test_each_deployment_resolves_to_its_own_values(solution):
    context = open_solution(solution).require_valid()
    resolved, _ = resolve_deployments(context.controller.index)
    assert sorted(resolved) == ["customer-acme-001-acmep01", "spoke-acme-001", "spoke-initech-001"]

    initech = resolved["spoke-initech-001"].model.spec
    assert (initech.tenant, initech.environments) == ("initech", ["solution-env", "hub-hub02-env"])
    assert initech.layers is not None and initech.layers.segments == {"hub": "hub02", "spoke": "initech-001"}
    assert initech.properties == {  # as the page shows it
        "region": "westus2",
        "subscription_id": "00000000-0000-0000-0000-000000000002",
        "state_account": "sttfstateinitech001",
    }
    assert resolved["spoke-acme-001"].model.spec.properties["region"] == "westeurope"  # type: ignore[index]

    customer = resolved["customer-acme-001-acmep01"].model.spec
    assert (customer.tenant, customer.environments) == ("acme", ["solution-env", "hub-hub01-env"])
    assert customer.properties == {
        "project": "p01",
        "deploy_code": "acmep01",
        "subscription_id": "00000000-0000-0000-0000-000000000001",
    }
