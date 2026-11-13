#!/usr/bin/env python3
"""The how-to `docs/how-to/environment-and-tenant-bases.md` builds a solution that validates.

Every ```yaml block of the page whose first line is `# <path>` is a file of that solution. The test writes them all
and loads the solution, so the page cannot show configuration strata would reject, and checks the merged documents
and the rendered origins the page describes.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from strata.controllers.deployment_resolution import resolve_deployments
from strata.controllers.rendered_view import render_document
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind

HOWTO = Path(__file__).resolve().parents[2] / "docs" / "how-to" / "environment-and-tenant-bases.md"

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


def _model(context: Any, kind: PlatformKind, name: str) -> Any:
    entry = context.controller.index.get(kind, name)
    assert entry is not None
    return entry.model


def test_the_page_shows_every_file_of_its_layout():
    files = set(_files())
    assert len(files) == 15
    layout = HOWTO.read_text(encoding="utf-8").split("## The layout", 1)[1].split("```", 2)[1]
    for line in layout.splitlines():
        name = line.split()[0] if line.strip() else ""
        if name.endswith(".yaml"):
            assert any(path.endswith(name) for path in files), name


def test_the_solution_validates_strictly_with_nothing_unused(solution):
    context = open_solution(solution).require_valid()
    assert not context.diagnostics.warnings  # `strata validate --strict` passes
    assert [f for f in context.diagnostics.infos if f.code == "param_unused"] == []


def test_tenants_and_environments_are_merged_as_the_page_says(solution):
    context = open_solution(solution).require_valid()
    initech = _model(context, PlatformKind.TENANT, "initech").spec
    assert initech.display_name == "Initech"
    assert initech.environments == ["customer-initech-env"]
    assert initech.properties == {"tier": "premium"}
    assert initech.configuration == {"dns_slug": "initech", "crm_id": "42"}
    assert _model(context, PlatformKind.TENANT, "acme").spec.properties == {"tier": "standard"}

    acme_env = _model(context, PlatformKind.ENVIRONMENT, "customer-acme-env").spec
    assert acme_env.properties == {"customer_code": "acme"}
    assert [(v.key, v.value) for v in acme_env.variables or []] == [("dns_zone", "acme.example.com")]
    assert [(f.key, f.value) for f in acme_env.features or []] == [("enable_sso", False)]  # a boolean, not text
    assert (_model(context, PlatformKind.ENVIRONMENT, "customer-initech-env").spec.features or [])[0].value is True

    resolved, _ = resolve_deployments(context.controller.index)
    assert resolved["app-initech"].model.spec.properties == {"tier": "premium"}  # the tenant's default
    assert resolved["app-initech"].model.spec.environments == ["customer-initech-env", "solution-env"]


def test_the_rendered_origins_are_as_the_page_shows(solution):
    context = open_solution(solution)
    rendered, _ = render_document(context.controller.index, PlatformKind.TENANT, "initech")
    assert rendered is not None
    fields = {origin.path: origin for origin in rendered.fields}
    tier = fields["spec.properties.tier"]
    assert (tier.value, tier.document, tier.written) == ("premium", "customer_tenant_base", "${param:customer.tier}")
    assert [read.parameter_set for read in tier.reads] == ["customer-initech"]
    assert fields["spec.configuration.crm_id"].document == "initech"
    assert rendered.param_sets == {"customer": ("customer-defaults", "customer-initech")}

    sso = next(
        origin
        for origin in render_document(context.controller.index, PlatformKind.ENVIRONMENT, "customer-acme-env")[0].fields  # type: ignore[union-attr]
        if origin.path == "spec.features[0].value"
    )
    assert (sso.value, [read.parameter_set for read in sso.reads]) == (False, ["customer-defaults"])

    deployment, _ = render_document(context.controller.index, PlatformKind.DEPLOYMENT, "app-initech")
    assert deployment is not None
    tier = next(origin for origin in deployment.fields if origin.path == "spec.properties.tier")
    assert (tier.document_kind, tier.document) == ("tenant", "customer_tenant_base")
