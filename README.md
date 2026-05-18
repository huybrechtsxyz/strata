# XYZ Platform

XYZ Platform is a **modular, multi-repository infrastructure platform** for managing workspaces and cluster orchestration. All infrastructure is defined in YAML; the `xyz` CLI orchestrates the full lifecycle from workspace initialization through Terraform provisioning.

📖 **[Full documentation](docs/README.md)**

---

## Quick Install

```bash
uv sync
```

**Linux / macOS:** `source .venv/bin/activate && xyz --help`

**Windows:** `.venv\Scripts\Activate.ps1` then `xyz --help`

Or without activating: `uv run xyz-platform --help`

---

## Quick Start

```bash
xyz init --name my-workspace
xyz repo add xyz-config git@github.com:org/xyz-config.git --branch main --clone
xyz profile add prd --activate
xyz ref config add global-config --path "@xyz-config/config/xyz-config.yaml"
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

See [docs/README.md#quick-start](docs/README.md#quick-start) for the full step-by-step walkthrough.

---

## Automation & AI Agents

Set `XYZ_OUTPUT=json` once (or `xyz config set output json`) and every command returns a structured JSON envelope on **stdout**:

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

| Concern | Guidance |
|---------|----------|
| Output format | Always use `--output json` or `XYZ_OUTPUT=json` |
| Exit codes | `0` success · `1` system failure · `2` bad args · `3` validation failure |
| Workspace | Set `XYZ_WORK_PATH` to target a workspace without `cd` |
| Error stream | In console mode, errors go to **stderr**; structured JSON always goes to **stdout** |
| Execution correlation | `execution_id` in the envelope maps directly to `xyz audit list --execution-id <id>` |
| Env-var overrides | Every CLI flag has an `XYZ_<OPTION>` equivalent (`XYZ_VERBOSE=true`, `XYZ_QUIET=true`, …) |

Safe read-only commands that work outside an initialized workspace (`INIT_REQUIRED=False`): `validate`, `status`, `schema list`, `schema get`.

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).
