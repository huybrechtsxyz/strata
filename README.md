# strata

You already have Terraform. The problem is you have eight environment folders: `dev`, `staging`, `prd`, `prd-eu`, `prd-us`, `dr`, `sandbox`, `perf` and they are 90% identical. Every change gets applied to one folder, forgotten in three others, and you only find out when production drifts. `strata` is a YAML layer over Terraform, Helm, and Compose that treats your environments as data, not copy-pasted folders. One source of truth, one command to validate it, one command to deploy it.

**This is v2**. A ground-up, evidence-driven rebuild of strata: every feature here is justified by v1's real source or a real consumer's real CI, not by assumption. It's alpha, so schemas and the CLI surface are still moving. See [docs/design/v2-schema-overview.md](docs/design/v2-schema-overview.md) for what's actually implemented today.

---

## Why a YAML layer over Terraform/Helm/Compose

- **One document model, many provisioners.** Providers, resources, networks, modules, and namespaces are declared once as plain YAML and rendered into Terraform, Helm, or Compose, you don't hand-roll the same VM, network, or module three times per environment.
- **Environments are data, not folders.** A deployment composes a workspace with an environment's variables/secrets and a tenant's defaults, promoting to a new environment is a small new YAML document, not a copy-pasted directory tree.
- **Validate before you touch anything.** Every document is schema-checked and cross-referenced (unresolved variables, missing modules, dangling version pins) before anything is rendered or applied.
- **Built to be scripted.** Structured JSON output and stable exit codes, so CI pipelines and AI agents can drive it without screen-scraping.

## Status

v2 is a rebuild, not a fork. [docs/decisions/](docs/decisions/) holds the full trail of architecture decision records (ADRs) behind it; [docs/design/](docs/design/) tracks living, current status per feature. See [.github/CHANGELOG.md](.github/CHANGELOG.md).

## Documentation

- [config/README.md](config/README.md): a real, working example solution used to dogfood the CLI end to end
- [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md): dev environment setup and contribution workflow
- [.github/CHANGELOG.md](.github/CHANGELOG.md) / [.github/HISTORY.md](.github/HISTORY.md): what's changed

## License

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).

