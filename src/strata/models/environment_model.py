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

from typing import Annotated, Any, ClassVar

from pydantic import ConfigDict, Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import References
from strata.models.store_model import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
)
from strata.models.template_model import DocumentTemplateModel, ParamSetNames, check_name_rule
from strata.utils.names import check_unique_names

_PARTIAL_DESCRIPTION = (
    "When true this is a base for other environments (`extends`), not an environment of its own: no deployment or "
    "tenant may list it in `environments`, and no `${value:}` token may read it."
)
_EXTENDS_DESCRIPTION = (
    "Name of a base Environment whose spec is merged into this one when the solution loads. Child always wins: "
    "`properties` and `custom` deep-merge per leaf key; a `variables`, `secrets` or `features` entry with the same "
    "`key` replaces the base's whole entry, and new keys are appended. Circular chains are rejected."
)
_PARAMS_DESCRIPTION = (
    "Parameter sets this environment reads, per role: `{customer: customer-acmep01}`, or a list per role merged in "
    "order. Any document of the extends chain may name sets: per role they merge root first, leaf last, later "
    "winning per leaf key. `${param:<role>.<key>}` in the spec of any document of the chain reads the merged values."
)
_NAME_RULE_DESCRIPTION = (
    "The name every environment built on this one must have, with `${param:<role>.<key>}` tokens filled from that "
    "environment's parameter sets (`deploy-${param:customer.code}-env`). Only `${param:}` tokens are allowed."
)


class EnvironmentSpecModel(PlatformBaseModel):
    """The values an environment supplies: the `variables`, `secrets` and `features` that `${var:KEY}`,
    `${secret:KEY}` and `${feature:KEY}` resolve against, plus shared `properties` and `custom` data.

    Terraform receives resolved `constant` and `environment` variables as `strata.variables` and
    features as `strata.flags`. Secrets are never written to disk; they are delivered at deploy time.

    Design notes:
    Environment specification: the values this environment supplies.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "Keys must be unique within each of `variables`, `secrets` and `features`. The same key "
        "may appear in different stores, because `${var:X}` and `${secret:X}` are different "
        "tokens.",
    )

    partial: bool = Field(default=False, description=_PARTIAL_DESCRIPTION)
    extends: Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)] | None = Field(
        None, description=_EXTENDS_DESCRIPTION
    )
    properties: dict[str, Any] | None = Field(
        None,
        description="Environment-wide properties, merged as a layer into the deployments that use "
        "this environment. Tenant properties merge first, then this environment's, "
        "then the deployment's own. The merged result is passed to Terraform as "
        "`strata.properties`.\n\n"
        "Design notes: Environment-wide properties, merged as a layer into deployments "
        "that use this environment. Tenant properties merge in first, then this, then "
        "the deployment's own.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values merged like `properties` across the tenant, environment and "
        "deployment, passed to Terraform as `strata.custom`.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Merges "
        "across the Tenant/Environment/Deployment chain like `properties`, delivered "
        "as one JSON-encoded `TF_VAR_custom` env var for the whole merged dict — not "
        "split into a separate env var per key.",
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
    params: ParamSetNames | None = Field(None, description=_PARAMS_DESCRIPTION)
    name_rule: str | None = Field(None, min_length=1, description=_NAME_RULE_DESCRIPTION)
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")

    @field_validator("name_rule")
    @classmethod
    def validate_name_rule_tokens(cls, value: str | None) -> str | None:
        """Only well-formed `${param:}` tokens: a run-time token has no value when names are checked."""
        return check_name_rule(value)


class EnvironmentModel(PlatformBaseModel):
    """A source of values for value tokens: variables, secrets and feature flags, plus shared properties. A
    deployment lists environments in `environments`, merged in order with later ones winning; a tenant
    can contribute environments that merge first.

    Design notes:
    Root model for an environment configuration file.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "An environment cannot extend itself; longer extends cycles are rejected when the solution loads.",
    )

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
    spec: EnvironmentSpecModel = Field(
        description="Environment specification (properties, variables, secrets, features)"
    )

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.ENVIRONMENT)

    @model_validator(mode="after")
    def validate_not_self_extending(self) -> "EnvironmentModel":
        """An environment cannot extend itself; longer cycles are rejected when the chain is resolved."""
        if self.spec.extends and self.spec.extends == self.meta.name:
            raise ValueError(f"Environment '{self.meta.name}' cannot extend itself.")
        return self


class EnvironmentTemplateSpecModel(PlatformBaseModel):
    """The spec of an environment template, as written: only `partial` and `extends` are read before substitution.

    Design notes:
    Every other field is kept as written and validated per environment that resolves through it, once `${param:}`
    is substituted (`base-parameters-v2.md` (work doc), "Templates are validated per instance").
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True, str_strip_whitespace=True)

    partial: bool = Field(default=False, description=_PARTIAL_DESCRIPTION)
    extends: Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)] | None = Field(
        None, description=_EXTENDS_DESCRIPTION
    )


class EnvironmentTemplateModel(DocumentTemplateModel):
    """An environment whose spec reads parameter sets (`${param:<role>.<key>}`): meta validated, spec as written.

    Design notes:
    Not a kind of its own and never exported as a schema: the loader picks it over `EnvironmentModel` when the spec
    holds a `${param:}` token. Resolution (`controllers/document_chains.py`) replaces a non-partial one in the index
    with the `EnvironmentModel` it resolves to.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for environment configuration"
    )
    kind: PlatformKind = Field(
        default=PlatformKind.ENVIRONMENT, frozen=True, description="Platform kind (always 'environment')"
    )
    meta: EnvironmentMetaModel = Field(description="Environment metadata (name, params, annotations, labels, tags)")
    spec: EnvironmentTemplateSpecModel = Field(description="Environment specification, as written")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.ENVIRONMENT)
