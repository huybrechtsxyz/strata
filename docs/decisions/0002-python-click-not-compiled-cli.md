# Python + Click for the CLI, not a compiled binary

- Status: completed
- Date: 2025-07-16

## Context and Problem Statement

strata is a DevOps CLI tool. The implementation language and CLI framework determine: install experience, cross-platform support, iteration speed, contributor accessibility, and long-term maintenance cost.

What language and CLI framework should strata use?

## Considered Options

- **Python + Click** — interpreted Python, Click for argument parsing and command structure
- **Go** — compiled binary, single executable distribution, fast startup
- **Rust** — compiled binary, very fast startup, strong type safety
- **Python + Typer** — Python with Typer (Click wrapper with type annotation-driven interface)

## Decision Outcome

Chosen: **Python + Click**, because strata's primary users are infrastructure engineers who already operate in Python-heavy environments (Ansible, Pulumi, Azure DevOps pipelines), and because the business logic — YAML parsing, Pydantic validation, subprocess orchestration — is most naturally expressed in Python. Click provides the exact CLI primitives needed (command groups, context passing, env var binding) without abstraction overhead.

### Consequences

- Good: Pydantic v2 for YAML validation is a natural fit — rich error messages, type coercion, and cross-field validators with minimal boilerplate.
- Good: `structlog` for structured logging integrates cleanly.
- Good: `uv` provides fast, reproducible installs with lockfile support — install experience is comparable to compiled tools.
- Good: Contributors familiar with Python can read, test, and modify the codebase without learning a new language.
- Good: `subprocess` for Terraform/Helm/Ansible invocation is straightforward — no FFI or cgo complexity.
- Bad: Python startup time (~100ms) is slower than a compiled binary. Acceptable for a deployment tool where operations take seconds to minutes.
- Bad: Requires Python 3.13 to be installed — one more prerequisite for end users (mitigated by dev container and `pipx install`).
- Bad: No single-binary distribution — users must have Python available (mitigated by `pipx install xyz-strata` which handles isolation).

## Pros and Cons of the Options

### Go

- Good: Single statically-linked binary — zero runtime dependencies for end users.
- Good: Fast startup, easy to distribute via GitHub releases.
- Bad: YAML + Pydantic-style validation in Go requires significantly more boilerplate (no equivalent of Pydantic v2).
- Bad: Contributor pool for a DevOps Python tool is smaller in Go.
- Bad: Cross-compiling for all target platforms adds CI complexity.

### Rust

- Good: Fastest startup, best binary size.
- Bad: Steepest learning curve — reduces contributor pool most severely.
- Bad: No mature equivalent of Pydantic for config validation.
- Neutral: Same distribution benefits as Go, with higher implementation cost.

### Python + Typer

- Good: Type annotation-driven interface reduces CLI boilerplate further.
- Bad: Typer is a thin wrapper over Click — adds a dependency without adding capabilities needed here.
- Bad: Typer's auto-generated help text is harder to customise precisely.
- Neutral: Would be equivalent to Click for most practical purposes; no strong reason to prefer it.

## More Information

Click's `auto_envvar_prefix` feature is heavily used — every option accepts a `STRATA_` environment variable automatically, enabling CI integration without explicit `--flag` passing. This feature is not available in Typer without custom wiring.

Related: [Getting Started](../platform/getting-started.md), [CI Integration](../platform/ci-integration.md)
