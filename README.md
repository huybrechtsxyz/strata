# strata

You already have Terraform. The problem is you have eight environment folders — `dev`, `staging`, `prd`, `prd-eu`, `prd-us`, `dr`, `sandbox`, `perf` — and they are 90% identical. Every change gets applied to one folder, forgotten in three others, and you only find out when production drifts. `strata` is a YAML config layer over Terraform, Helm, and scripts that treats your environments as data, not as copy-pasted folders. One source of truth, one command to validate it, one command to deploy it.

📖 **[Full documentation](https://huybrechtsxyz.github.io/strata)**

**Support:** [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)

---

## Quick Start

```bash
pipx install xyz-strata                        # install
strata sln init                                             # scaffold a new config workspace
strata console                                              # interactive guided session
strata validate --file stack/my-environment.yaml        # validate before you touch anything
strata deploy run --file deploy/my-environment.yaml     # deploy to the target environment
```

New here? See the [feature overview](docs/guides/features.md) for a practical rundown of what strata does, or the [Getting Started guide](docs/platform/getting-started.md) for the full walkthrough. Run `strata --help` to see all commands. Dev install: `uv sync`.

**No PyPI access? Using a Dev Container?** Inject strata from the published image — no Python install required:

```dockerfile
# .devcontainer/Dockerfile
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04
COPY --from=ghcr.io/huybrechtsxyz/strata:latest /app/.venv /opt/strata
ENV PATH="/opt/strata/bin:$PATH"
```

See the [Getting Started guide](docs/platform/getting-started.md#install) for the full devcontainer and air-gapped registry setup.

---

## Automation & AI Agents

Set `STRATA_OUTPUT=json` once (or `strata config set output json`) and every command returns a structured JSON envelope on **stdout**:

```json
{
  "success": true,
  "command": "validate",
  "execution_id": "fb063135-...",
  "timestamp": "2026-05-18T14:31:47.267924",
  "data": { "validation_passed": true, "errors": [] },
  "messages": [],
  "errors": []
}
```

Key conventions for automation:

| Concern               | Guidance                                                                                        |
| --------------------- | ----------------------------------------------------------------------------------------------- |
| Output format         | Always use `--output json` or `STRATA_OUTPUT=json`                                              |
| Exit codes            | `0` success · `1` system failure · `2` bad args · `3` validation failure                        |
| Workspace             | Set `STRATA_WORK_PATH` to target a workspace without `cd`                                       |
| Error stream          | In console mode, errors go to **stderr**; structured JSON always goes to **stdout**             |
| Execution correlation | `execution_id` in the envelope maps directly to `strata audit list --execution-id <id>`         |
| Env-var overrides     | Every CLI flag has an `XYZ_<OPTION>` equivalent (`STRATA_VERBOSE=true`, `STRATA_QUIET=true`, …) |

Safe read-only commands that work outside an initialized workspace (`INIT_REQUIRED=False`): `validate`, `status`, `schema list`, `schema get`.

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).
