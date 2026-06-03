---
agent: agent
description: "Scaffold a new strata Module YAML file for a service"
---

Create a new `kind: Module` YAML file for the service named **${input:moduleName}**.

Ask the user for:
1. **Deployer type** — `compose` or `helm`
2. **Services** — list of container names and images (e.g. `app: myapp:1.0`, `db: postgres:16`)
3. **Extra files** — any config files to copy into the build output (e.g. `traefik.yaml`, `conf.d/*`)
4. **Output path** — where to write the file (default: `modules/${input:moduleName}.yaml`)

Then generate the file following the strata YAML rules:
- `meta.name` must be lowercase with no spaces
- Each service gets a `name` and `image` field
- If extra files were requested, add a `spec.files` block with `source` and `target` entries
- Glob sources require `target` to end with `/`

After writing the file, run:
```bash
strata validate <output_path>
```

Show the user the validation result. If it fails (exit code 3), fix the errors and re-validate.
