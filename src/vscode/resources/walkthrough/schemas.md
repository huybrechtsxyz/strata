# Wire YAML Schemas

Run **Strata: Export & Wire Schemas** to enable editor support for all strata YAML files.

This does two things:

1. **Exports** JSON schemas to `.strata/schemas/` for every document kind (workspace, configuration, deployment, module, etc.)
2. **Wires** them into `.vscode/settings.json` via the `yaml.schemas` setting

## What you get

- **Autocomplete** for `kind`, `apiVersion`, `meta.name`, and every `spec` field
- **Red squiggles** on unknown fields — catches mistakes before you run validate
- **Hover documentation** from schema `description` fields
- **Enum suggestions** for valid values (e.g., provisioner types, resource kinds)

> **Prerequisite:** Install the [YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml) for schema-driven editing.
