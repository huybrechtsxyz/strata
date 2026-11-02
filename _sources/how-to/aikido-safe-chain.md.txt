# How To: Install strata in a pipeline that uses Aikido Safe Chain

[Aikido Safe Chain](https://github.com/AikidoSec/safe-chain) wraps `pip`, `pip3`, `uv`, `uvx`,
`pipx`, `poetry` and `pdm` (and the npm family) on laptops and in CI, and blocks known-malicious
packages. Two of its defaults affect a repository that installs strata (`xyz-strata`). Both are
settings in the consumer repository, not in strata.

## 1. Exclude strata from the 48-hour package age

By default Safe Chain hides releases younger than 48 hours from the resolver. For two days after
a strata release:

- an unpinned `pip install xyz-strata` silently installs the **previous** version;
- a pinned install of the new version fails.

Exclude strata in the repository's `.aikido` file:

```yaml
safe-chain:
  pip:
    minimumPackageAgeExclusions:
      - xyz-strata
```

## 2. Register the private feed

strata is installed from the private Python feed, which Safe Chain must know about:

- list the feed under `customRegistries`, in `.aikido` or through the
  `SAFE_CHAIN_PIP_CUSTOM_REGISTRIES` environment variable;
- if pip reads its index from a `pip.conf`, point `PIP_CONFIG_FILE` at it. Otherwise Safe
  Chain's temporary pip configuration replaces yours.

## Notes

- Not yet confirmed in a real pipeline (haven, cfg-int). The settings come from Safe Chain's own
  README (2026-10-26).
- Untested: how Safe Chain combines with uv's `--index-strategy unsafe-best-match`, which an
  install from the private feed may need.
- Background: [docs/archive/aikido-integration.md](../archive/aikido-integration.md), "Gotchas found".
