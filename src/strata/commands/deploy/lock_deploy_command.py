"""Commands for inspecting and managing deployment state locks."""

import os
import socket
from typing import Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockBackendError,
)
from strata.models.common_models import ProvisionerType

# ---------------------------------------------------------------------------
# Shared backend resolution
# ---------------------------------------------------------------------------


def _resolve_lock_backend(deployment_service, work_path) -> BaseLockBackend:  # type: ignore[no-untyped-def]
    """Return the lock backend for the deployment.

    Reads the first Terraform provisioner with a backend from the workspace
    service.  Falls back to ``LocalLockBackend`` when no Terraform provisioner
    is found.
    """
    from strata.integrations.lock.lock_factory import LockFactory

    try:
        workspace_service = deployment_service.get_workspace_service()
    except Exception:  # noqa: BLE001
        return LockFactory.create(None, work_path)

    if workspace_service is not None:
        spec = workspace_service.model.spec  # type: ignore[union-attr]
        provisioners = spec.provisioners or []
        for prov in provisioners:
            if prov.provisioner == ProvisionerType.TERRAFORM and prov.backend:
                return LockFactory.create(prov.backend, work_path)

    return LockFactory.create(None, work_path)


# ---------------------------------------------------------------------------
# strata deploy lock status
# ---------------------------------------------------------------------------


class LockStatusCommand(BaseDeployCommand):
    """Query the current lock state for a deployment."""

    OPERATION = "deploy_lock_status"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            ok = self._run_status()

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_lock_status: {exc}")
            self.logger.exception("deploy_lock_status failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Status logic
    # -------------------------------------------------------------------------

    def _run_status(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deploy_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]

        try:
            backend = _resolve_lock_backend(self._deployment_service, self._work_path)
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            return False

        try:
            entry = backend.status(deploy_name)
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            return False

        if entry is None:
            if self._is_console_output():
                click.echo(f"\n✅  '{deploy_name}' is not locked.")
            else:
                self._output_data["locked"] = False
                self._output_data["deployment"] = deploy_name
            self.logger.info("deploy_lock_status_unlocked", deployment=deploy_name)
            return True

        # Locked — render details
        if self._is_console_output():
            click.echo(f"\n🔒  '{deploy_name}' is locked")
            click.echo(f"    lock_id    : {entry.lock_id}")
            click.echo(f"    holder     : {entry.holder}")
            click.echo(f"    hostname   : {entry.hostname}")
            click.echo(f"    pid        : {entry.pid}")
            click.echo(f"    acquired_at: {entry.acquired_at}")
            click.echo(f"    expires_at : {entry.expires_at}")
            click.echo(f"    reason     : {entry.reason}")
        else:
            self._output_data.update(
                {
                    "locked": True,
                    "deployment": deploy_name,
                    "lock_id": entry.lock_id,
                    "holder": entry.holder,
                    "hostname": entry.hostname,
                    "pid": entry.pid,
                    "acquired_at": entry.acquired_at,
                    "expires_at": entry.expires_at,
                    "reason": entry.reason,
                }
            )

        self.logger.info(
            "deploy_lock_status_locked",
            deployment=deploy_name,
            lock_id=entry.lock_id,
            holder=entry.holder,
        )
        return True


# ---------------------------------------------------------------------------
# strata deploy lock release
# ---------------------------------------------------------------------------


class LockReleaseCommand(BaseDeployCommand):
    """Release the state lock for a deployment.

    Without ``--force``, release is denied if the lock is held by a different
    user/host (exit code 3 — lock contention).

    With ``--force``, the lock is released regardless of holder.
    """

    OPERATION = "deploy_lock_release"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        force: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._force = force
        self._contention = False

    # -------------------------------------------------------------------------
    # Exit code: contention → exit 3 (same as validation failure)
    # -------------------------------------------------------------------------

    def has_validation_errors(self) -> bool:
        return self._contention

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            ok = self._run_release()

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_lock_release: {exc}")
            self.logger.exception("deploy_lock_release failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Release logic
    # -------------------------------------------------------------------------

    def _run_release(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deploy_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]

        try:
            backend = _resolve_lock_backend(self._deployment_service, self._work_path)
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            return False

        # Read current lock state
        try:
            entry = backend.status(deploy_name)
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error reading status: {exc}")
            return False

        if entry is None:
            if self._is_console_output():
                click.echo(f"\nℹ️   '{deploy_name}' is not locked — nothing to release.")
            self.logger.info("deploy_lock_release_not_locked", deployment=deploy_name)
            return True

        # Determine if this is our own lock
        current_holder = (
            os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        )
        current_host = socket.gethostname()
        is_own_lock = entry.holder == current_holder or entry.hostname == current_host

        if not is_own_lock and not self._force:
            self._errors.append(
                f"Lock is held by '{entry.holder}' on {entry.hostname} "
                f"(acquired {entry.acquired_at}). "
                "Use --force to release another holder's lock."
            )
            self._contention = True
            if self._is_console_output():
                click.echo(f"\n⛔  Lock held by '{entry.holder}' on {entry.hostname}. Use --force to override.")
            return False

        # Perform force-release (releases by name regardless of handle)
        try:
            backend.force_release(deploy_name)
        except LockBackendError as exc:
            self._errors.append(f"Lock release failed: {exc}")
            return False

        action = "force-released" if self._force and not is_own_lock else "released"
        if self._is_console_output():
            click.echo(f"\n🔓  Lock {action} for '{deploy_name}'.")
            if not is_own_lock:
                click.echo(f"    ⚠️  Previous holder: '{entry.holder}' on {entry.hostname} (lock_id: {entry.lock_id})")
        else:
            self._output_data.update(
                {
                    "released": True,
                    "deployment": deploy_name,
                    "lock_id": entry.lock_id,
                    "previous_holder": entry.holder,
                    "forced": self._force and not is_own_lock,
                }
            )

        self.logger.info(
            "deploy_lock_released_by_command",
            deployment=deploy_name,
            lock_id=entry.lock_id,
            holder=entry.holder,
            forced=self._force and not is_own_lock,
        )
        return True


# ---------------------------------------------------------------------------
# strata deploy lock history
# ---------------------------------------------------------------------------


class LockHistoryCommand(BaseDeployCommand):
    """Show recent lock history for a deployment."""

    OPERATION = "deploy_lock_history"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        last: int = 10,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._last = last

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            ok = self._run_history()

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_lock_history: {exc}")
            self.logger.exception("deploy_lock_history failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # History logic
    # -------------------------------------------------------------------------

    def _run_history(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deploy_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]

        try:
            backend = _resolve_lock_backend(self._deployment_service, self._work_path)
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            return False

        try:
            entries = backend.history(deploy_name, limit=self._last)
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            return False

        if not entries:
            if self._is_console_output():
                click.echo(f"\nℹ️   No lock history found for '{deploy_name}'.")
            else:
                self._output_data["deployment"] = deploy_name
                self._output_data["entries"] = []
            return True

        if self._is_console_output():
            click.echo(f"\n🔒  Lock history for '{deploy_name}' (last {len(entries)}):\n")
            _col = "  {:<36}  {:<20}  {:<12}  {:<24}  {}"
            click.echo(_col.format("lock_id", "holder", "pid", "acquired_at", "reason"))
            click.echo("  " + "-" * 110)
            for e in entries:
                click.echo(
                    _col.format(
                        e.lock_id[:36],
                        (e.holder or "")[:20],
                        str(e.pid),
                        (e.acquired_at or "")[:24],
                        (e.reason or "")[:60],
                    )
                )
        else:
            self._output_data["deployment"] = deploy_name
            self._output_data["entries"] = [
                {
                    "lock_id": e.lock_id,
                    "holder": e.holder,
                    "hostname": e.hostname,
                    "pid": e.pid,
                    "acquired_at": e.acquired_at,
                    "expires_at": e.expires_at,
                    "reason": e.reason,
                    "stage": e.stage,
                }
                for e in entries
            ]

        self.logger.info(
            "deploy_lock_history_shown",
            deployment=deploy_name,
            count=len(entries),
        )
        return True
