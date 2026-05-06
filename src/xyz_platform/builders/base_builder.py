"""Base class for deployment builders."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from xyz_platform.logger import get_logger
from xyz_platform.services.deployment_service import DeploymentService


class BaseBuilder(ABC):
    """Abstract base class for workspace builders."""

    def __init__(self, verbose: bool = False) -> None:
        self.logger = get_logger(self.__class__.__module__)
        self.verbose = verbose
        self._messages: List[str] = []
        self._errors: List[str] = []

    def has_errors(self) -> bool:
        return len(self._errors) > 0

    def has_messages(self) -> bool:
        return len(self._messages) > 0

    def get_messages(self) -> List[str]:
        return self._messages

    def get_errors(self) -> List[str]:
        return self._errors

    @abstractmethod
    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """Build the workspace according to the builder's logic."""
        raise NotImplementedError

    @abstractmethod
    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
    ) -> bool:
        """Hook executed before the build process starts."""
        raise NotImplementedError

    @abstractmethod
    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """Hook executed after the build process completes."""
        raise NotImplementedError
