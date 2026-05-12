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

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).
