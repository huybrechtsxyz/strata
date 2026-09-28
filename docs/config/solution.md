# Solution (`kind: solution`)

The bootstrap document — `strata.yaml` at the solution root. Marks "a strata solution starts
here" and supplies the minimum needed to find everything else: discovery rules and remotes live
downstream of it, never inside it. Found by walking up from any path (like `go.mod`/`package.json`),
not by discovery itself.

## Schema

- `spec.configuration` — relative path to this solution's `configuration.yaml` (adjacent by convention)
- `spec.discovery.exclude` — glob patterns excluded from document discovery, additive to the
  loader's built-in ignores (`.git`, `build`, `.strata`, tool caches)
- `spec.remotes[]` — named external artifact sources, each:
  - `name` — referenced elsewhere as `@<name>/<path>`
  - `type` — `local`, `git`, `oci`, or `helm`
  - `url` — the source location
  - `fetch` — `strata` (strata materializes it) or `external` (already placed by CI/elsewhere)
  - `description` — optional, human-readable

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: example
  annotations:
    description: "Example solution used to exercise the discovery loader and the full strata build run pipeline end to end"
spec:
  configuration: configuration.yaml
  discovery:
    exclude:
      - "templates/**"
  remotes:
    - name: infra
      type: local
      url: vendor/infra
    - name: charts
      type: helm
      url: https://charts.example.io
    - name: bundled
      type: local
      url: modules
```

## Notes

- Scope discipline: this document answers "what/where is this solution", never "what rules
  govern it" — platform policy (provider/topology registries) stays in `Configuration`.
- Remotes supply *artifacts* (Terraform modules, charts, copied files), not strata *documents* —
  a `Module`/`ProviderConfig` document must live in the solution's own discovery tree.
- No lock file: `reference` (a remote's pinned ref, see [version.md](version.md)) is declared
  here or overridden by a `version` document; the checkout location is a CLI convention, not a
  second recorded fact.
