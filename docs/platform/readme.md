# Platform Reference

This section documents the internal layers of the XYZ Platform CLI.

## What's Here

| Document | Contents |
| -------- | -------- |
| [commands.md](commands.md) | Full CLI command reference — every command group, option, and exit code |
| [workflow.md](workflow.md) | End-to-end DevOps workflow guide (init → validate → build → deploy) |
| [cli-preferences.md](cli-preferences.md) | Persistent CLI defaults via env vars or workspace config |
| [architecture.md](architecture.md) | Multi-repository structure and deployment workflow overview |
| [exit-codes.md](exit-codes.md) | Exit code definitions and per-command usage table |
| [models.md](models.md) | Pydantic v2 model schema, field constraints, and validation phases |
| [services.md](services.md) | Service layer — BaseService, load/cache lifecycle, two-phase validation |
| [configuration.md](configuration.md) | ConfigurationService — load order, repo map, merge strategy |
| [integrations.md](integrations.md) | Integration layer — factory, singleton lifecycle, subprocess wrapping |
| [builders.md](builders.md) | Build pipeline — PlatformBuilder, TerraformBuilder, artifact output |
| [deployers.md](deployers.md) | Deploy pipeline — deployer table, step constants, execution order |
| [validators.md](validators.md) | Validation pipeline — BaseValidator, PlatformValidator |
| [lifecycles.md](lifecycles.md) | Lifecycle hooks — phase naming, script execution, env variables |
| [exceptions.md](exceptions.md) | Exception hierarchy — PlatformError subclasses and usage |
| [logging.md](logging.md) | Structured logging — get_logger, structlog config, YAML config files |
| [utilities.md](utilities.md) | Pure utility functions — path resolution, system helpers |

## Navigation

- **New to the project?** Start with [workflow.md](workflow.md) for the full operational walkthrough.
- **Writing CLI commands?** See [commands.md](commands.md) for all flags and exit codes.
- **Working on models or services?** See [models.md](models.md) and [services.md](services.md).
- **Debugging build/deploy?** See [builders.md](builders.md) and [deployers.md](deployers.md).
