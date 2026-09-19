"""Shared stage resolution for commands that act on deployment stages (ADR-0083 D7).

Before this, seven commands hand-rolled the same four blocks — the null-service
guard, the ``spec.stages`` extraction, the ``--stage``/``--scope`` filter, and the
error routing — and had drifted into three different error messages between them.

The selection *algorithm* lives in :mod:`strata.utils.stage_selection`; this mixin
is only the command-side plumbing that feeds it. That split is required, not
stylistic: ``DeploymentService`` also calls the algorithm, and a service importing
from the command layer would invert ADR-0003's dependency direction.
"""

from typing import TYPE_CHECKING, List, Optional

from strata.utils.stage_selection import StageSelection, StageSelectionMode, select_stages

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from strata.services.deployment_service import DeploymentService
    from strata.utils.resolved_values import ResolvedValues


class StageSelectionMixin:
    """Gives a command ``_resolve_stages()``.

    Inherited by both ``BaseDeployCommand`` and ``BaseBuildCommand`` — stage
    selection is not a concern of every command, so this is not on ``BaseCommand``.

    Opt-in, not automatic: eleven commands set ``_stage`` but only seven filter by
    it this way, so inheriting the mixin changes nothing until a command actually
    calls :meth:`_resolve_stages`.
    """

    # Declared rather than read via getattr, so a command that renamed one of
    # these fails type-checking instead of silently selecting every stage.
    #
    # The split is deliberate. `_stage`/`_scope` default to None because they are
    # genuinely optional — most commands have no --scope at all. `_deployment_service`
    # and `_errors` are annotation-only, so a subclass that fails to set them raises
    # AttributeError: that is a programming error and should fail loudly rather than
    # be reported to the user as "Deployment service not loaded".
    _stage: Optional[str] = None
    _scope: Optional[str] = None
    _deployment_service: Optional["DeploymentService"]
    _errors: List[str]

    def _resolve_stages(
        self,
        mode: StageSelectionMode,
        *,
        resolved: Optional["ResolvedValues"] = None,
    ) -> Optional[StageSelection]:
        """Return the stages this invocation should act on.

        Args:
            mode: What the caller intends to do with them — see
                :class:`~strata.utils.stage_selection.StageSelectionMode`.
                ``DEPLOY`` honours ``enabled`` and orders by ``depends_on``;
                ``DESTROY`` and ``INSPECT`` do neither, for different reasons.
            resolved: Values used to evaluate ``enabled`` expressions. Required
                only when *mode* gates and a stage uses an expression.

        Returns:
            The selection, or ``None`` when the caller should abort — in which
            case the reason has already been appended to ``self._errors``.
        """
        model = self._deployment_service.model if self._deployment_service else None
        if model is None:
            self._errors.append("Deployment service not loaded")
            return None

        selection, errors = select_stages(
            model.spec.stages or [],
            stage=self._stage,
            scope=self._scope,
            resolved=resolved,
            mode=mode,
        )
        if errors:
            self._errors.extend(errors)
            return None
        return selection
