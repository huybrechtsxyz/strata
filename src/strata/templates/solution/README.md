# ${SOLUTION_NAME}

Configuration repository managed by [strata](https://github.com/huybrechtsxzy/strata).

## Getting Started

**Prerequisites:** `strata` installed (`uv tool install xyz-strata`) and `git`, `terraform` on PATH.

**First time setup:**

```bash
# Register this repo as the config source
strata repo add ${SOLUTION_NAME}_config <repo-url> --branch main --clone

# Add a profile and point it at your config files
strata profile add <environment> --activate
strata ref config add ${SOLUTION_NAME}-config --path "@${SOLUTION_NAME}_config/config/${SOLUTION_NAME}-config.yaml"
strata ref env add ${SOLUTION_NAME}-env --path "@${SOLUTION_NAME}_config/environments/${SOLUTION_NAME}-env-<environment>.yaml"
```

**Day-to-day:**

```bash
strata status                                                  # workspace overview
strata validate run -f deployments/<deployment>.yaml           # lint & validate
strata build run -f deployments/<deployment>.yaml              # build artifact
strata deploy run -f deployments/<deployment>.yaml             # provision & deploy
```
