# Kubernetes-style YAML schema for config documents

- Status: accepted
- Date: 2025-07-16

## Context and Problem Statement

strata needs a schema for its configuration documents (workspace, deployment, environment, etc.). The schema must be:
- Human-readable and writable without special tooling
- Parseable by standard libraries in any language
- Extensible without breaking existing documents
- Validatable at build time with structural and cross-reference checks

What format and structure should strata use for its YAML documents?

## Considered Options

- **Kubernetes-style YAML** (`apiVersion`, `kind`, `meta`, `spec`) — same structure as Kubernetes manifests
- **Flat custom YAML** — bespoke top-level keys per document type (e.g., `name:`, `environment:`, `modules:`)
- **HCL (HashiCorp Configuration Language)** — same language as Terraform
- **TOML** — machine-friendly, common in Python tooling

## Decision Outcome

Chosen: **Kubernetes-style YAML**, because it provides a proven, immediately recognisable structure that separates identity (`meta`) from configuration (`spec`), supports versioning (`apiVersion`) for non-breaking schema evolution, and is already familiar to the DevOps engineers who are strata's primary users.

### Consequences

- Good: `apiVersion` allows future schema versions without breaking existing files — old documents declare which schema they follow.
- Good: `kind` enables a single YAML parser to handle all document types via dispatch on the `kind` field.
- Good: Operators already know this structure from Kubernetes manifests, Helm values, and Kustomize — zero conceptual overhead.
- Good: `meta.name` with enforced naming constraints (`PlatformName`) makes documents uniquely identifiable and cross-referenceable by name.
- Good: IDE tooling (JSON Schema + `redhat.vscode-yaml`) gives autocomplete and validation for free using the same pattern.
- Bad: More verbose than a flat schema — a minimal document requires `apiVersion`, `kind`, `meta.name`, and `spec`.
- Bad: Engineers unfamiliar with Kubernetes may find the structure unfamiliar initially.

## Pros and Cons of the Options

### Flat custom YAML

- Good: Less verbose for simple documents.
- Bad: No built-in versioning — breaking schema changes require migrating all existing files simultaneously.
- Bad: No standard dispatch mechanism — the parser must infer document type from file path or content heuristics.
- Bad: Harder to tooling-support without a well-known structure.

### HCL

- Good: Native to the Terraform ecosystem strata integrates with.
- Bad: HCL parsing libraries outside Go and Ruby are second-class citizens — Python support is fragile.
- Bad: Conflates infrastructure code (Terraform `.tf` files) with deployment configuration — the separation of concerns is a core design principle.
- Bad: HCL is not JSON-Schema compatible — VS Code autocomplete requires a custom language server.

### TOML

- Good: Clean syntax, good Python library support.
- Bad: Poor support for deeply nested structures (topology, stages, modules).
- Bad: No established convention for document typing or versioning.
- Bad: Not familiar to infrastructure engineers — different mental model from existing tooling.

## More Information

The `apiVersion` field follows the domain `platform.huybrechts.xyz/v1`. When breaking changes are required, `v2` documents can coexist with `v1` documents during a migration window. The `kind` field maps to a specific Pydantic model class via the service layer.

Related: [Configuration Format docs](../config/configuration.md), [Models reference](../platform/models.md)
