"""Utility functions shared across Ansible builder and deployer components."""

from pathlib import Path
from typing import Optional

_REQUIREMENTS_CANDIDATES = ("requirements.yml", "collections/requirements.yml")


def find_ansible_requirements_file(directory: Path) -> Optional[Path]:
    """Return the path to the Galaxy requirements file within an Ansible directory.

    Checks ``requirements.yml`` then ``collections/requirements.yml`` under
    *directory*.  Returns ``None`` if neither file exists.

    Used by both ``AnsibleDeployer`` and ``AnsibleCollectionCollector`` so that
    the discovery convention lives in exactly one place.
    """
    for candidate in _REQUIREMENTS_CANDIDATES:
        path = directory / candidate
        if path.exists():
            return path
    return None
