# Reuben — History

## Core Context

Docs / Technical Writer for strata. Sphinx docs, Markdown guides, CLI reference.
User: Vincent Huybrechts. Stack: Sphinx, reStructuredText, Markdown.
Key paths: `docs/`, `docs/conf.py`, `docs/index.rst`, `docs/cli-preferences.md`, `docs/SQUAD.md`.

**Past documentation (condensed):**
- 2026-05-18: Devcontainer scaffold docs — `commands.md` init section rewrite + "Files created" table; `workflow.md` Phase 1 extended with devcontainer files
- 2026-05-19: sln group docs — `getting-started.md` updated `xyz init` → `xyz sln init`; `copilot-instructions.md` updated registered CLI groups list
- 2026-05-19: README pitch rewrite + Getting Started guide — pain-statement pitch, 4-command Quick Start, new `docs/platform/getting-started.md`
- 2026-05-29: github secret store doc — new `## Secret Stores` section in `configuration.md`; `integrations.md` note that `github` + `environment` are built-in resolvers; `store: github` reads env vars uppercased, not a real integration
- 2026-06-01: ComposeBuilder docs — `builders.md` new `## ComposeBuilder` section: output path, service naming rules, env sources, volumes, healthchecks, example, pipeline summary
- 2026-06-01: HelmBuilder docs — `builders.md` new `## HelmBuilder` section mirroring ComposeBuilder structure; `meta.yaml` contents table; PVC persistence block explanation
- 2026-06-01: Helm deployer/integration audit — gap analysis: missing HelmDeployer section in `deployers.md`, missing HelmIntegration in `integrations.md`, wrong capability (IContainerTool → IInfrastructureTool), no `type: helm` in `deployment.md`
- 2026-06-02: Helm docs implementation — `deployers.md` HelmDeployer section + overview row; `integrations.md` HelmIntegration row + section + corrected capability; `deployment.md` Stage Types table + workflow step 5 update; `commands.md` deploy note generalized
- 2026-06-02: ComposeDeployer docs — `deployers.md` ComposeDeployer section (alphabetical before Helm); `deployment.md` compose row in Stage Types; `integrations.md` "Used by ComposeDeployer" sentence
- 2026-06-09: DNS kind documentation — new `docs/config/dns.md`; `config/readme.md` updated; `commands.md` valid kinds + new example; `workflow.md` Phase 6.2 callout with firewall + dns
- 2026-06-09: DNS record value/var/secret docs — `dns.md` 6 targeted edits: references block, union record fields table, var:/secret: subsections, example updated, validation rules table

### 2026-06-10 — Network kind documentation

- **`docs/config/network.md`** — Created new reference doc. Structure mirrors `dns.md`: What it is / When to use; top-level schema block; six field tables (top-level, spec.references, network, CidrSource, subnet, peering); CIDR overlap detection section with three tiers (subnet-to-subnet, containment, cross-network peered vs non-peered); cross-kind references section (`<network>/<subnet>` format on `WorkspaceResourceModel.subnet`); variable and secret CIDRs comparison table and subsections; two worked examples (haven flat network, enterprise hub-spoke with var refs and peerings); linking to workspace; build output (`networks.auto.tfvars.json`); full 13-rule validation table (V1–V13); merge behaviour table; template reference; best practices.
- **`docs/config/readme.md`** — Added `network.md` to the Resources kind listing, positioned after `firewall.md` (alphabetical order: n after f).
- **`docs/platform/commands.md`** — Two changes: (1) Added `network` to the **Valid kinds** list in `schema get KIND` (alphabetical: between `namespace` and `platform_model`); (2) Added `strata new network my-networks --path config/networks/` to the `strata new` usage examples.
- **`docs/platform/workflow.md`** — Extended the Phase 6.2 resource kinds callout to include network topologies (`spec.networks` → `networks.auto.tfvars.json`) with a note about CIDR overlap detection. Added cross-link to `network.md`.
- **Key structural decision:** CIDR overlap detection got its own narrative section (not just validation table rows) because the three tiers of validation (intra-network subnet overlap, subnet containment within address space, cross-network peered vs non-peered) need explanation of *why* — operators need to understand the difference between a warning and an error for overlapping address spaces.

### 2026-06-10 — Document `strata guide` in commands.md

- **`docs/platform/commands.md`** — Two changes: (1) Added `guide` row to the Command Groups table, positioned after `validate` (no `†` marker — INIT_REQUIRED = False); (2) Inserted a new `## guide` section between `## validate` and `## schema`.
- **Section structure:** Syntax block → options table (all five flags including `--file / -f`) → exit code callout → `### Workspace mode` (7-phase checklist table + console output example from spec section 4 + next-step hints table) → `### File mode (--file)` (5-phase structural table + file mode console example + `@repo/path` note) → `### Project customisation` (`.strata/guide.yaml` override pattern) → usage examples.
- **Console output examples:** Used the spec section 4 examples verbatim, including the partial-clone ⚠️ and empty-refs ⚠️ scenario for workspace mode, and the configuration kind example for file mode. Real examples with concrete values (prd, xyz-svc-traefik) are more useful than abstract placeholders.
- **Phase table design:** Added separate ✅ / ⚠️ / ⬜ columns to the workspace checklist table rather than inline prose — lets operators scan quickly which states each phase supports. Note: phases 2, 4, 5 never produce ⚠️ (binary done/not-done), so those cells show —.
- **Key convention:** `guide` is a top-level command (not under `sln`) because it is an onboarding entry point before users know any group names. INIT_REQUIRED = False mirrors StatusCommand and HelpCommand — always mention this in docs via "Works outside an initialized workspace" callout.

## Learnings

- **Union fields in record tables:** When a model field becomes a union (one-of), the Required column should change from "Yes"/"No" to "one of" to accurately signal mutual exclusivity. This pattern works for any doc where Pydantic discriminates exactly one field from a set.
- **spec.references placement:** A top-level spec block's dedicated reference section belongs directly after the top-level fields table, not buried after the sub-object (zone/record) sections. Readers correlate the top-level table row to the section that follows.
- **secret: records and Terraform variable naming:** The `dns_secret_records` variable is a Terraform-side concern; the doc must tell users they need to wire it in their HCL — strata only omits the value from tfvars and names the env var `TF_VAR_<key>`. Always document the "other side" of the boundary.
- **Preserving comprehensive examples:** When updating an existing worked example, keep the full richness and just modify/add the records needed to show new patterns. A minimal replacement example loses the coverage that operators use as a copy-paste starting point.
- **Network kind documentation approach:** The network kind has more validation depth than DNS or firewall (CIDR overlap detection, containment, peering-aware errors). Dedicated sections for each validation tier (subnet-to-subnet, containment, cross-network) are clearer than cramming everything into the validation rules table. The validation rules table gets all 13 rules as a quick-reference, while the "CIDR Overlap Detection" narrative section explains the *why* for operators who need to understand what strata catches. Two-example strategy (haven + enterprise hub-spoke) covers both the simple and complex use cases without requiring a third example.

