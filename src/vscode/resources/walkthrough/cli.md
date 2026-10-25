# Install the strata CLI

The extension never re-implements strata: every diagnostic and every graph edge comes from the
CLI you deploy with, so the editor and your pipeline always agree.

Install it as a tool:

```sh
pipx install xyz-strata
# or
uv tool install xyz-strata
```

Or keep it per project. The extension finds `strata` in the workspace `.venv` or `venv`
automatically (for example after `uv sync`), so there is nothing to configure.

Using something else, like `uv run strata`? Set **`strata.cliPath`**.

> **Check:** the status bar shows `strata 2.x.x` once the CLI is found. Hover it to see why if it
> isn't.
