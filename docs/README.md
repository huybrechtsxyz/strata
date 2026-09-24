# strata-v2

Strata v2 is a **modular infrastructure-as-code platform** for managing
workspaces and cluster orchestration, redesigned from the ground up based on
lessons learned from v1 (see [docs/decisions](decisions/0001-v1-schema-analysis-findings-for-v2.md)).
For the current state of each kind/component (as opposed to the historical
decision record), see [docs/design](design/README.md), particularly
[docs/design/v2-schema-overview.md](design/v2-schema-overview.md).

All infrastructure is defined in YAML using Pydantic-validated schemas
(`apiVersion`, `kind`, `meta`, `spec`).

## Status

This project is in early development. See the repository README for the
current project structure and setup instructions.
