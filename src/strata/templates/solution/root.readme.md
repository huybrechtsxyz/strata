# ${SOLUTION_NAME}

Configuration repository managed by [strata](https://github.com/huybrechtsxzy/strata).

## Getting Started

**Prerequisites:** `strata` installed (`uv tool install strata`) and `git`, `terraform` on PATH.

**First time setup:**

```bash
# Register this repo as the config source
xyz repo add ${SOLUTION_NAME}_config <repo-url> --branch main --clone

# Add a profile and point it at your config files
xyz profile add <environment> --activate
xyz ref config add ${SOLUTION_NAME}-config --path "@${SOLUTION_NAME}_config/config/${SOLUTION_NAME}-config.yaml"
xyz ref env add ${SOLUTION_NAME}-env --path "@${SOLUTION_NAME}_config/environments/${SOLUTION_NAME}-env-<environment>.yaml"
```

**Day-to-day:**

```bash
xyz status                                                  # workspace overview
xyz validate run -f deployments/<deployment>.yaml           # lint & validate
xyz build run -f deployments/<deployment>.yaml              # build artifact
xyz deploy run -f deployments/<deployment>.yaml             # provision & deploy
```
