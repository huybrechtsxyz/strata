#!/usr/bin/env python3
"""Shared manifest-artifact collection logic used by both ``build run`` and
``deploy run``/``deploy destroy``.

Both command families assemble the same artifact BOM (platform hash,
repository provenance, provisioner metadata) for ``ManifestArtifactsModel`` —
this module is the single source of truth for that logic so a future manifest
schema change only needs to happen once. Image info is deliberately NOT here:
it's deploy-only (sourced from runtime stage outputs that don't exist at
build time) and stays on ``BaseDeployCommand``.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

from strata.models.deployment_manifest_model import (
    ManifestArtifactProviderModel,
    ManifestPlatformModel,
    ManifestRepositoryModel,
)
from strata.models.solution_model import SolutionModel
from strata.services.deployment_service import DeploymentService


def collect_platform_artifact(
    deployment_service: Optional[DeploymentService],
    build_path: Path,
    work_path: Path,
) -> ManifestPlatformModel:
    """Compute SHA-256 of platform.json and embed its full content."""
    if deployment_service is None:
        return ManifestPlatformModel(hash="unknown")

    platform_path = deployment_service.get_build_path(build_path) / "platform.json"
    if not platform_path.exists():
        return ManifestPlatformModel(hash="unknown")

    content_bytes = platform_path.read_bytes()
    digest = hashlib.sha256(content_bytes).hexdigest()
    rel_path = str(platform_path.relative_to(work_path))
    try:
        content = json.loads(content_bytes.decode("utf-8"))
    except Exception:
        content = None

    return ManifestPlatformModel(hash=f"sha256:{digest}", path=rel_path, content=content)


def collect_repository_info(
    solution: Optional[SolutionModel],
    repo_map: Optional[Dict[str, str]],
) -> Optional[Dict[str, ManifestRepositoryModel]]:
    """Walk solution repositories and collect URL/ref/commit info.

    Takes the already-resolved ``solution`` model and ``repo_map`` (rather than a
    ``SolutionController`` instance) so this ``services/`` module never depends on
    ``controllers/`` (ADR-0003) — callers (``commands/``) already hold the controller
    and can pass ``solution_controller.solution`` / ``solution_controller.get_repo_map()``.
    """
    if solution is None:
        return None

    repos = solution.spec.repositories or []
    if not repos:
        return None

    result: Dict[str, ManifestRepositoryModel] = {}
    for repo in repos:
        name = str(repo.name)
        url = getattr(repo, "url", None)
        ref = getattr(repo, "ref", None)
        commit: Optional[str] = None

        if repo_map and name in repo_map:
            repo_path = Path(repo_map[name])
            head_file = repo_path / ".git" / "HEAD"
            if head_file.exists():
                try:
                    head_content = head_file.read_text(encoding="utf-8").strip()
                    if head_content.startswith("ref:"):
                        ref_path = repo_path / ".git" / head_content[5:]
                        if ref_path.exists():
                            commit = ref_path.read_text(encoding="utf-8").strip()
                    else:
                        commit = head_content  # detached HEAD = commit SHA
                except OSError:
                    pass

        result[name] = ManifestRepositoryModel(
            url=str(url) if url else None,
            ref=str(ref) if ref else None,
            commit=commit,
        )

    return result if result else None


def collect_provider_info(
    deployment_service: Optional[DeploymentService],
) -> Optional[List[ManifestArtifactProviderModel]]:
    """Collect provisioner metadata from the workspace model.

    Walks ``workspace.spec.provisioners`` and captures each provisioner's
    name, tool type, and state backend configuration.
    """
    if deployment_service is None:
        return None
    workspace_service = deployment_service.get_workspace_service()
    if workspace_service is None or workspace_service.model is None:
        return None

    provisioners = getattr(workspace_service.model.spec, "provisioners", None) or []
    if not provisioners:
        return None

    result: List[ManifestArtifactProviderModel] = []
    for prov in provisioners:
        backend_dict: Optional[dict] = None
        if getattr(prov, "backend", None) is not None:
            backend_dict = {
                "type": prov.backend.type,
                "configuration": prov.backend.configuration,
            }

        details: Optional[dict] = None
        if getattr(prov, "properties", None) is not None:
            details = prov.properties.model_dump(exclude_none=True)

        result.append(
            ManifestArtifactProviderModel(
                name=str(prov.name),
                type=prov.provisioner,
                backend=backend_dict,
                details=details,
            )
        )

    return result if result else None
