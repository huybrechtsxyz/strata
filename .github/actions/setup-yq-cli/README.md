# Setup YQ

Install `yq` (Mike Farah version) for YAML processing in CI workflows.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-yq@v1
  with:
    yq-version: "v4.44.6"
```

## Inputs

| Input        | Required | Default   | Description           |
| ------------ | -------- | --------- | --------------------- |
| `yq-version` | No       | `v4.44.6` | yq version to install |

## Notes

Required by `get-deployment-info` and `get-deployment-name`, which parse deployment YAML files with `yq`.
