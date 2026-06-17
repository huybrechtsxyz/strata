# Strict layered architecture (commands → controllers → services)

- Status: accepted
- Date: 2025-07-16

## Context and Problem Statement

As strata grows, multiple CLI commands need to share business logic (e.g., both `build run` and `diff` need to load and validate a deployment file). Without a clear structure, logic migrates into command handlers, creating duplication, tight coupling, and commands that are impossible to unit-test without a real filesystem or subprocess.

How should strata organise its internal code to support reuse, testability, and clear contributor guidance?

## Considered Options

- **Strict layers** — `commands/` → `controllers/` → `services/` → `integrations/` → `models/` → `utils/`, with enforced one-way dependencies
- **Feature modules** — group by feature (e.g., `build/`, `deploy/`) with all layers inside each module
- **Flat structure** — all logic in command handlers, no enforced layering

## Decision Outcome

Chosen: **Strict layers**, because the dependency direction (higher layers call lower, never the reverse) makes every layer independently testable, and the boundaries are clear enough that a contributor can understand where new code belongs without reading the whole codebase.

### Consequences

- Good: Command handlers are thin — they parse CLI input and delegate to a `BaseCommand` subclass. This means the same command logic is callable from tests, scripts, or other commands without invoking Click.
- Good: Services are purely about loading and validating a single YAML model type — no subprocess calls, no CLI concerns. They can be unit-tested with fixture files.
- Good: Integrations are the only layer that calls subprocess — all other layers receive results. Mocking `self._run_integration()` in tests prevents real Terraform/Bitwarden calls.
- Good: Controllers accumulate errors into `self._errors` rather than raising — callers decide what to do with failures rather than having to catch exceptions from deep in the stack.
- Bad: More files and indirection than a flat structure — a simple new feature touches 3-4 files minimum.
- Bad: The layering constraint requires discipline — static analysis (`mypy`) does not enforce it automatically.

## Layer Responsibilities

| Layer           | Owns                                                    | Never does                      |
| --------------- | ------------------------------------------------------- | ------------------------------- |
| `commands/`     | Click wiring, `--flag` parsing, exit codes              | Business logic, YAML loading    |
| `controllers/`  | Orchestrating services + integrations for one operation | YAML loading, subprocess calls  |
| `services/`     | Loading + validating one YAML model type                | Subprocess calls, Click imports |
| `integrations/` | Subprocess wrappers for one external tool               | YAML loading, service calls     |
| `models/`       | Pydantic schema for one document kind                   | Filesystem access, subprocess   |
| `utils/`        | Pure functions                                          | Any import from layers above    |

## More Information

The `BaseCommand` class handles the common lifecycle: `_initialize()` → `_before_execute()` → `_run_execution()` → `_after_execute()` → `_finalize()`. Subclasses override only the phases they need. This means audit logging, error formatting, and output serialisation happen once in the base class.

`BaseService.load(path)` handles caching via `service_cache.py` — the same deployment file loaded twice in one execution returns the same object. Controllers rely on this to avoid redundant I/O.

Related: [Architecture overview](../platform/architecture.md), [Commands reference](../platform/commands.md), [Services internals](../platform/services.md)
