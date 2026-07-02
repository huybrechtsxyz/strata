# Initialize Your Workspace

Run **Strata: Initialize Workspace** to create the `.strata/` directory and solution registry.

This sets up:

- `.strata/solution.json` — the solution registry that tracks repositories, profiles, and workspace state
- `.strata/cli.yaml` — workspace defaults for the CLI

## Next steps

1. Add a configuration repository: `strata repo add --name <name> --path <path>`
2. Create a profile: `strata profile create --name dev`
3. Activate it: `strata profile activate dev`

> **Tip:** Run `strata guide show` at any time to see what's left to do.
