#!/usr/bin/env python3
"""Pydantic model for environment configuration validation.

An `Environment` supplies **values**: the variables, secrets and feature flags
that `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` tokens resolve against
(ADR-0002). It is the document every other kind's Phase 2 token check has
been waiting for.

**This is deliberately a small slice of v1's `environment_model.py`, and the
evidence says it should be.** v1's version is by far its largest schema —
`EnvironmentOverridesModel` alone nests six sub-models (`resources`,
`modules` with per-service image overrides, `providers`, `includes`,
`remotes`, `output_files`), plus `lifecycle` and `promotion`. Scanning all
**26 real environment documents** across two production repos, the spec
fields actually used are:

===============  =====
field            uses
===============  =====
``secrets``      26
``variables``    25
``features``     23
``properties``   23
``overrides``     0
``lifecycle``     0
``promotion``     0
===============  =====

So the entire overrides subtree — the complicated part — has zero real
usage. Porting it would be modelling machinery nothing exercises, against
ADR-0003's minimal-slice policy. Deferred items, each with a real reason
beyond "unused":

- ``overrides.resources``/``.modules``/``.providers`` — per-environment
  overrides of workspace content. Genuinely useful in principle (dev pins
  ``:latest``, prod pins ``:1.2.3``), but they need a merge/build layer to
  mean anything, and v2 has none.
- ``overrides.remotes`` — overrides a remote's ``reference`` per
  environment. Worth noting this does **not** contradict ADR-0015's
  declaration-only rule: that forbids *per-use-site* overrides, which let two
  modules silently pull different trees at once. A per-environment override
  keeps "one remote, one tree" within an environment while letting dev and
  prod differ — a real and different thing. Re-add when Deployment exists to
  select an environment.
- ``overrides.includes``/``.output_files`` — Terraform file merging and
  output shaping; pure build machinery (``OutputFileModel`` was not ported
  either, see provisioning).
- ``lifecycle``/``promotion`` — no hook-execution machinery; promotions are
  deferred Configuration surface (ADR-0003).

No cloud tags (ADR-0017): an Environment is a value source, not a
provisioned resource.
"""

from typing import Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.store_model import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
)
from strata.utils.names import check_unique_names


class EnvironmentSpecModel(PlatformBaseModel):
    """Environment specification: the values this environment supplies."""

    properties: dict[str, Any] | None = Field(
        None,
        description="Environment-wide properties, merged as a layer into deployments that use this "
        "environment. Tenant properties merge in first, then this, then the deployment's own.",
    )
    custom: dict[str, Any] | None = Field(
        None, description="Custom user-defined data for scripts or extensions (e.g. becomes env vars)"
    )
    variables: list[VariableStoreModel] | None = Field(
        None, description="Variable declarations — what '${var:KEY}' tokens resolve against"
    )
    secrets: list[SecretStoreModel] | None = Field(
        None, description="Secret declarations — what '${secret:KEY}' tokens resolve against"
    )
    features: list[FeatureStoreModel] | None = Field(
        None, description="Feature flag declarations — what '${feature:KEY}' tokens resolve against"
    )

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "EnvironmentSpecModel":
        """Keys must be unique within each store.

        Checked per store rather than globally: `${var:X}` and `${secret:X}`
        are different tokens, so the same key name in both is legitimate.
        """
        if self.variables:
            check_unique_names([v.key for v in self.variables], "variable keys in environment")
        if self.secrets:
            check_unique_names([s.key for s in self.secrets], "secret keys in environment")
        if self.features:
            check_unique_names([f.key for f in self.features], "feature keys in environment")
        return self


class EnvironmentMetaModel(PlatformBaseModel):
    """Environment metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique environment name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class EnvironmentModel(PlatformBaseModel):
    """Root model for an environment configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for environment configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.ENVIRONMENT,
        frozen=True,
        description="Platform kind (always 'environment')",
    )
    meta: EnvironmentMetaModel = Field(description="Environment metadata (name, annotations, labels, tags)")
    spec: EnvironmentSpecModel = Field(description="Environment specification (properties, variables, secrets, features)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.ENVIRONMENT)
