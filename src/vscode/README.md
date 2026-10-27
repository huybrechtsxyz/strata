<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img src="https://raw.githubusercontent.com/huybrechtsxyz/strata/main/src/vscode/resources/icon.png" width="112" alt="Strata">

# Strata for VS Code

**Your environments are data, not copy-pasted folders.<br>Catch a broken one while you type, not
after you deploy.**

[Get started](#get-started) · [Features](#features) · [Commands](#commands) ·
[Settings](#settings) · [Upgrading from v1](#upgrading-from-v1) ·
[Troubleshooting](#troubleshooting)

</div>

---

## Why Strata

You already have Terraform. The problem is the eight environment folders, `dev`, `staging`, `prd`,
`prd-eu`, `dr` and the rest, that are 90% identical. A change lands in one, gets forgotten in three
others, and you find out when production drifts.

[Strata](https://github.com/huybrechtsxyz/strata) is a YAML layer over **Terraform**, **Helm** and
**Docker Compose** that treats environments as data. You describe providers, workspaces, modules
and environments once; a deployment combines them, and one command validates the whole solution
before anything is rendered or applied.

This extension brings those checks into the editor, so you see a mistake while you are editing.

## Features

### Validate the whole solution on every save

Save any YAML file and the extension runs `strata validate` across the **entire** solution, since
a rename in one document can break references in five others. Schema errors, references to
documents that don't exist, variables nothing provides and dangling version pins appear in the
**Problems** panel, on the field that caused them.

### See how your documents connect

The **Graph** view lists every cross-document reference: which deployment uses which workspace,
which workspace uses which providers and modules. References that don't resolve are flagged, and a
click takes you straight to the field. Use it to answer *"what breaks if I rename this?"* before
you rename it.

### Integrations and policies at a glance

Pick a deployment from the status bar (**Strata: Select Deployment**) and two more views follow
it. **Integrations** shows every integration the deployment needs, whether its tool is installed
at the right version, and what uses it; the plug button runs its connection check. **Policies**
shows each policy's phase, enforcement and last recorded result; the play button checks it now,
and the list button shows its CVE, Checkov or image findings right in the tree.

### Autocomplete for every document kind

**Strata: Update Solution Files** exports a JSON Schema for each document kind. Together with the
[YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml), you get
completion, hover documentation and inline errors for every `spec` field as you type.

### An AI agent that already knows strata

The same command gives your repository an agent skill that **Claude Code** and **GitHub Copilot**
load automatically: the commands, the merge rules, the known pitfalls, and the rule to never deploy
or change secrets unless asked. A second skill holds your team's own lessons, and strata never
overwrites it.

### The real CLI, never a re-implementation

Every check is the `strata` CLI you deploy with, so the editor and your pipeline never disagree.
The status bar shows which CLI version is answering, and the extension finds it in your project's
`.venv` without any configuration.

## Get started

1. **Install the CLI** (version 2 or later):

   ```sh
   pipx install xyz-strata        # or: uv tool install xyz-strata
   ```

   A project-local install in `.venv`/`venv` (for example from `uv sync`) is found automatically.

2. **Open a folder that contains `strata.yaml`.** No solution yet? Create one:

   ```sh
   strata sln init my-platform
   ```

3. **Save a YAML file.** Findings appear in the **Problems** panel (`Ctrl+Shift+M`), and the
   status bar shows `strata 2.x.x`.

4. **Run *Strata: Update Solution Files*** once, to turn on autocomplete and the agent skills.

Prefer a guided tour? Open **Help → Welcome** and choose **Get started with Strata**.

**Requirements:** VS Code 1.90 or later, the `strata` CLI 2.x, and (for autocomplete) the
[YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml).

## Commands

Open the Command Palette (`Ctrl+Shift+P`) and type **Strata**.

| Command                           | What it does                                                                                                                                     |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Strata: Validate Solution**     | Validate the whole solution now and show a summary.                                                                                              |
| **Strata: Refresh Graph**         | Reload the Graph view after larger edits.                                                                                                        |
| **Strata: Select Deployment**     | Choose the deployment the Integrations and Policies views show. Also on the status bar.                                                          |
| **Strata: Check Policies**        | Run `strata policy check` for the selected deployment and record the results.                                                                    |
| **Strata: Update Solution Files** | Run `strata sln update`: refresh the JSON Schemas and the strata agent skill, and add any missing starter files. Asks before it writes anything. |

**Update Solution Files** always rewrites `.strata/`. It rewrites the strata agent skill
(`.claude/skills/strata/`) only while that file still has its "Generated by strata" line. It
creates missing `.vscode/`, `.github/` and `.claude/` starter files but never overwrites one that
exists. Every file it touches is listed in the **Strata** output channel.

## Settings

| Setting                 | Default    | Description                                                                                                       |
| ----------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------- |
| `strata.cliPath`        | `"strata"` | The CLI to run. The default looks in the workspace `.venv`/`venv` first, then `PATH`. Example: `"uv run strata"`. |
| `strata.validateOnSave` | `true`     | Re-validate the whole solution whenever a YAML file is saved.                                                     |

## Upgrading from v1

Version 2 of this extension is built for **strata v2** solutions. It is a rebuild, not an update,
so check this before you upgrade:

- **It needs a v2 solution and CLI.** The extension starts on a `strata.yaml` manifest (v1 used
  `.strata/solution.json`) and talks to the `strata` CLI 2.x. The status bar shows which CLI
  version it found; a 1.x version there means the CLI needs upgrading too.
- **Fewer, sharper features.** v2 starts with validation, the reference graph, schemas and agent
  skills. v1's Workspace, Files, Repositories, Environments, Tools and Audit views, the Build and
  Deploy commands, CodeLens, tasks, diagrams and the `@strata` chat participant are not part of
  2.0. Build, deploy and audit run from the CLI (`strata build run`, `strata deploy run`,
  `strata audit status`). For AI help, the agent skills replace the chat participant.

**Still on a v1 solution?** Stay on v1: in the Extensions view, open the gear menu on **Strata**,
choose **Install Specific Version…**, pick **1.11.2**, then turn off **Auto Update** for it.

## Troubleshooting

<details>
<summary><b>The status bar shows an error next to <code>strata</code></b></summary>

The CLI could not be run. Hover the status bar item for the exact error. Usually:

- `strata` is not on `PATH` and there is no workspace `.venv`/`venv`: install it (see
  [Get started](#get-started)) or set `strata.cliPath`, for example `"uv run strata"`.
- `strata.cliPath` points at something that isn't the strata CLI: check the spelling.

</details>

<details>
<summary><b>The extension never starts</b></summary>

It starts when the opened folder contains a `strata.yaml`, at its root or in a subfolder. The CLI
itself also finds a `strata.yaml` in a *parent* folder, but VS Code only looks downward, so open
the folder that contains the manifest (or a parent of it).

</details>

<details>
<summary><b>The Graph view is empty</b></summary>

- Run **Strata: Refresh Graph**. The view doesn't refresh by itself yet.
- The solution may have no cross-document references yet. A lone provider that nothing uses
  produces an empty graph, which is correct.
- If `strata graph` fails, a notification explains why.

</details>

<details>
<summary><b>Validation results look stale</b></summary>

Saves are debounced by 300 ms and each one validates the whole solution, so when several files are
saved together only the last result is shown, and it covers all of them. Run
**Strata: Validate Solution** to check immediately.

</details>

<details>
<summary><b>No autocomplete in YAML files</b></summary>

- Install the [YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml).
- Run **Strata: Update Solution Files** to export the schemas to `.strata/schemas/`.
- Check that `.vscode/settings.json` maps `.strata/schemas/strata.json` under `yaml.schemas`.
  `strata sln init` adds that mapping; older solutions may need it added by hand.

</details>

## Feedback and contributing

- **Bugs and ideas:** [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)
- **What's new:** [Changelog](https://github.com/huybrechtsxyz/strata/blob/main/src/vscode/CHANGELOG.md)
- **Build it yourself:** [Development guide](https://github.com/huybrechtsxyz/strata/blob/main/src/vscode/DEVELOPMENT.md)
- **How strata works:** [Documentation](https://github.com/huybrechtsxyz/strata/tree/main/docs)

## License

[AGPL-3.0-or-later](https://github.com/huybrechtsxyz/strata/blob/main/LICENSE). Strata for VS Code
is part of the [strata](https://github.com/huybrechtsxyz/strata) project.
