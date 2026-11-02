# Validate your solution

**Strata: Validate Solution** runs `strata validate` over the whole solution, not just the file in
front of you. That is the point: a rename in one document can break a reference in five others.

What it catches before anything is rendered or deployed:

- Schema errors: unknown fields, wrong types, missing required values
- References to documents that don't exist
- Variables and secrets that nothing provides
- Version pins that point nowhere

Findings land in the **Problems** panel (`Ctrl+Shift+M`), grouped by file and placed on the field
that caused them.

> **Tip:** you rarely need the command. Saving any YAML file re-validates the solution. Turn that
> off with **`strata.validateOnSave`**.
