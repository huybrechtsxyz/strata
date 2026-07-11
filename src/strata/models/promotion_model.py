#!/usr/bin/env python3
"""Pydantic models for the promotion strategy system (ADR-0011 Phase 1)."""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName


# ─── Ring-level models ────────────────────────────────────────────────────────


class ProgressionRingEnvironmentModel(PlatformBaseModel):
    """A single environment entry within a progression ring, with optional wave index."""

    name: str = Field(description="Environment name matching environment.meta.name")
    wave: Optional[int] = Field(
        None,
        ge=1,
        description="Intra-ring wave index (1-based). Environments with the same wave advance together.",
    )


class ProgressionRingModel(PlatformBaseModel):
    """A single ring within a promotion progression."""

    name: PlatformName = Field(description="Unique ring name within this progression (e.g. dev, test, prd)")
    environments: List[Union[ProgressionRingEnvironmentModel, str]] = Field(
        min_length=1,
        description=(
            "Environments belonging to this ring. "
            "Bare strings are equivalent to {name: <string>} with no wave assignment."
        ),
    )
    require: Optional[Literal["any_one", "all"]] = Field(
        None,
        description=(
            "Quorum gate for this ring: 'any_one' requires at least one environment in the previous "
            "ring to carry the version before promoting here; 'all' requires every environment. "
            "Absent on the first ring (no inbound requirement)."
        ),
    )
    require_lock: Optional[bool] = Field(
        None,
        description=(
            "When true, any 'strata build run' or 'strata deploy run' targeting an environment in this ring "
            "will fail (exit 3) if the ring's lock file (versions/{ring}.yaml) does not exist. "
            "Equivalent to passing --require-lock on the CLI but declared in configuration."
        ),
    )
    require_digests: Optional[bool] = Field(
        None,
        description=(
            "When true, 'strata validate --deep' will fail (exit 3) if any pin in the ring's lock file "
            "is missing a resolved_sha value. "
            "Enforces that all pins carry an immutable artifact reference."
        ),
    )

    @field_validator("environments", mode="before")
    @classmethod
    def coerce_environment_strings(cls, v: Any) -> Any:
        """Coerce bare strings to ProgressionRingEnvironmentModel dicts."""
        if isinstance(v, list):
            return [{"name": item} if isinstance(item, str) else item for item in v]
        return v

    def environment_names(self) -> List[str]:
        """Return a flat list of environment names in this ring."""
        result = []
        for env in self.environments:
            if isinstance(env, ProgressionRingEnvironmentModel):
                result.append(env.name)
        return result


class ProgressionModel(PlatformBaseModel):
    """A named promotion progression — an ordered sequence of rings."""

    name: PlatformName = Field(description="Unique progression name (e.g. standard, hotfix)")
    rings: List[ProgressionRingModel] = Field(
        min_length=1,
        description="Ordered list of rings. First ring has no inbound requirement; subsequent rings may set require.",
    )

    @model_validator(mode="after")
    def validate_unique_ring_names(self) -> "ProgressionModel":
        """Validate that ring names are unique within this progression."""
        seen: set = set()
        for ring in self.rings:
            if ring.name in seen:
                raise ValueError(f"Duplicate ring name '{ring.name}' in progression '{self.name}'")
            seen.add(ring.name)
        return self

    def ring_names(self) -> List[str]:
        """Return ring names in order."""
        return [ring.name for ring in self.rings]


# ─── Strategy models ──────────────────────────────────────────────────────────


class PromotionWaveModel(PlatformBaseModel):
    """A named deployment wave within a promotion strategy."""

    name: str = Field(
        min_length=1,
        description=(
            "Wave name (e.g. canary, early, all). "
            "Used in CLI --wave argument and matched against deployment wave assignment."
        ),
    )


class PromotionGatesModel(PlatformBaseModel):
    """Gate conditions that must be satisfied before a promotion can proceed."""

    require_progression_order: Optional[bool] = Field(
        None,
        description=(
            "When true, enforce ring ordering: the previous ring's quorum (defined by ring.require) "
            "must be satisfied before this ring can be promoted. "
            "Pure YAML inspection — no external tool integration required in Phase 1."
        ),
    )


class PromotionStrategyModel(PlatformBaseModel):
    """A named promotion strategy governing how a versioned artifact moves through rings."""

    name: PlatformName = Field(description="Unique strategy name (e.g. infra-cautious, app-wave)")
    type: Literal["remote", "helm_chart", "image", "module"] = Field(
        description=(
            "What this strategy promotes: "
            "'remote' — spec.overrides.remotes[].reference; "
            "'helm_chart' — module chart_version; "
            "'image' — module service image tags; "
            "'module' — generic module (legacy; prefer helm_chart or image for new strategies)"
        )
    )
    progression: str = Field(
        min_length=1,
        description=(
            "Name of the progression this strategy uses "
            "(must match a progression name in spec.promotions.progressions)"
        ),
    )
    waves: Optional[List[PromotionWaveModel]] = Field(
        None,
        description=(
            "Ordered deployment waves within each ring step. "
            "Deployments without a wave assignment default to the last wave (catch-all). "
            "Omit for all-at-once promotion (single implicit wave per ring step)."
        ),
    )
    scope: Optional[str] = Field(
        None,
        description=(
            "Layer scope for deployment wave targeting. "
            "Only deployments in the matching layer participate in gradual rollout. "
            "Example: 'tenant' — only tenant-layer deployments are waved; "
            "zone-layer infrastructure is always all-at-once."
        ),
    )
    gates: Optional[PromotionGatesModel] = Field(
        None,
        description="Gate conditions evaluated before a promotion step proceeds.",
    )

    @model_validator(mode="after")
    def validate_unique_wave_names(self) -> "PromotionStrategyModel":
        """Validate that wave names are unique within this strategy."""
        if self.waves:
            seen: set = set()
            for wave in self.waves:
                if wave.name in seen:
                    raise ValueError(f"Duplicate wave name '{wave.name}' in strategy '{self.name}'")
                seen.add(wave.name)
        return self


# ─── Configuration-level container ────────────────────────────────────────────


class ConfigurationPromotionsModel(PlatformBaseModel):
    """Promotion system configuration: progressions and strategies.

    Defined at ``configuration.spec.promotions``.

    Example::

        spec:
          promotions:
            progressions:
              - name: standard
                rings:
                  - name: dev
                    environments: [dev1, dev2]
                  - name: prd
                    environments:
                      - { name: prod-be, wave: 1 }
                      - { name: prod-us, wave: 2 }
                    require: any_one
            strategies:
              - name: infra-cautious
                type: remote
                progression: standard
                waves:
                  - name: canary
                  - name: all
                scope: tenant
                gates:
                  require_progression_order: true
    """

    progressions: Optional[List[ProgressionModel]] = Field(
        None,
        description="Named ring progressions. Each defines the ordered sequence of environments a version traverses.",
    )
    strategies: Optional[List[PromotionStrategyModel]] = Field(
        None,
        description=(
            "Named promotion strategies. "
            "Each strategy references a progression and defines deployment wave behaviour."
        ),
    )

    @model_validator(mode="after")
    def validate_unique_names(self) -> "ConfigurationPromotionsModel":
        """Validate unique progression and strategy names."""
        errors = []
        if self.progressions:
            seen: set = set()
            for p in self.progressions:
                if p.name in seen:
                    errors.append(f"Duplicate progression name '{p.name}'")
                seen.add(p.name)
        if self.strategies:
            seen = set()
            for s in self.strategies:
                if s.name in seen:
                    errors.append(f"Duplicate strategy name '{s.name}'")
                seen.add(s.name)
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_strategy_progression_refs(self) -> "ConfigurationPromotionsModel":
        """Validate that every strategy references a defined progression."""
        if not self.strategies or not self.progressions:
            return self
        progression_names = {p.name for p in self.progressions}
        errors = []
        for strategy in self.strategies:
            if strategy.progression not in progression_names:
                errors.append(
                    f"Strategy '{strategy.name}' references unknown progression '{strategy.progression}'. "
                    f"Known progressions: {sorted(progression_names)}"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self


# ─── Environment-level promotion membership ───────────────────────────────────


class EnvironmentPromotionModel(PlatformBaseModel):
    """Declares which promotion strategy and ring this environment belongs to.

    Defined at ``environment.spec.promotion``.

    Example::

        spec:
          promotion:
            strategy: infra-cautious
            ring: prd
    """

    strategy: str = Field(
        min_length=1,
        description=(
            "Name of the promotion strategy this environment participates in "
            "(must match a strategy in configuration.spec.promotions.strategies)"
        ),
    )
    ring: str = Field(
        min_length=1,
        description="Ring name within the strategy's progression that this environment is a member of",
    )


# ─── Deployment-level wave assignment ─────────────────────────────────────────


class DeploymentPromotionWaveModel(PlatformBaseModel):
    """Wave assignment for a deployment within a promotion ring step.

    Exactly one of ``iteration`` or ``match_labels`` should be set.
    ``iteration`` takes precedence when both are present.
    Deployments without this block default to the last wave (catch-all).

    Example — explicit position::

        spec:
          promotion:
            wave:
              iteration: 1    # always the canary

    Example — label-based assignment::

        spec:
          promotion:
            wave:
              match_labels: { tier: standard }
    """

    iteration: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Explicit wave position (1-based). "
            "Matches the wave at this index in the strategy's waves list."
        ),
    )
    match_labels: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Label selector placing this deployment in the first wave whose match_labels "
            "is a subset of this deployment's meta.labels. "
            "Resolution order: iteration wins over match_labels."
        ),
    )

    @model_validator(mode="after")
    def validate_at_least_one(self) -> "DeploymentPromotionWaveModel":
        """At least one of iteration or match_labels must be set."""
        if self.iteration is None and self.match_labels is None:
            raise ValueError("spec.promotion.wave requires at least one of 'iteration' or 'match_labels'")
        return self


class DeploymentPromotionModel(PlatformBaseModel):
    """Promotion metadata for a deployment — opt-in wave assignment.

    Defined at ``deployment.spec.promotion``.

    Example::

        spec:
          promotion:
            wave:
              iteration: 1
    """

    wave: Optional[DeploymentPromotionWaveModel] = Field(
        None,
        description=(
            "Wave assignment for this deployment within a promotion ring step. "
            "Deployments without a wave block default to the last (catch-all) wave."
        ),
    )
