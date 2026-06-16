"""Factory that selects a lock backend from a provisioner's backend configuration."""

from pathlib import Path
from typing import Optional

from strata.integrations.lock.base_lock_backend import BaseLockBackend
from strata.integrations.lock.lock_azurerm import AzurermLockBackend
from strata.integrations.lock.lock_consul import ConsulLockBackend
from strata.integrations.lock.lock_local import LocalLockBackend
from strata.integrations.lock.lock_tfc import TfcLockBackend
from strata.models.workspace_model import WorkspaceIacBackendModel


class LockFactory:
    """Selects and instantiates a lock backend from a provisioner's backend config.

    **Phase 2:** ``azurerm``, ``terraform_cloud`` / ``remote``, and ``consul``
    are implemented.  ``s3`` and ``gcs`` remain Phase 3 stubs that raise
    ``NotImplementedError``.

    Usage::

        backend = LockFactory.create(provisioner.backend, work_path)
        handle  = backend.acquire(deployment_name, holder, reason, timeout)
        try:
            ...
        finally:
            backend.release(handle)
    """

    @staticmethod
    def create(
        backend_model: Optional[WorkspaceIacBackendModel],
        work_path: Path,
    ) -> BaseLockBackend:
        """Return the appropriate ``BaseLockBackend`` for the given backend model.

        Args:
            backend_model: The provisioner's resolved backend configuration, or
                ``None`` when no backend is declared (Ansible/script stages).
            work_path: Workspace root — passed through to ``LocalLockBackend``.

        Returns:
            A concrete ``BaseLockBackend`` ready for use.

        Raises:
            NotImplementedError: When a remote backend type is requested but its
                Phase 2 implementation is not yet available.
        """
        if backend_model is None:
            return LocalLockBackend(work_path)

        backend_type = backend_model.type

        match backend_type:
            case "local":
                return LocalLockBackend(work_path)

            case "azurerm":
                return AzurermLockBackend(backend_model.configuration, work_path)

            case "terraform_cloud" | "remote":
                return TfcLockBackend(backend_model.configuration, work_path)

            case "s3":
                raise NotImplementedError(
                    "S3LockBackend is not yet implemented (Phase 3). "
                    "Set spec.locking.enabled: false or use a local backend."
                )

            case "consul":
                return ConsulLockBackend(backend_model.configuration, work_path)

            case "gcs":
                raise NotImplementedError(
                    "GcsLockBackend is not yet implemented (Phase 3). "
                    "Set spec.locking.enabled: false or use a local backend."
                )

            case _:
                # Unknown type — fall back to local rather than hard-failing,
                # so new Terraform backend types don't break the pipeline.
                return LocalLockBackend(work_path)
