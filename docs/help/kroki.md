# Kroki Integration

Kroki (https://kroki.io) renders diagram text (Mermaid) to SVG/PNG images. strata
uses it for `strata diagram show --format svg|png` — exporting a rendered diagram
as an image file instead of the default Mermaid source text.

No account, no API key, no CLI install
Rendering is a single HTTP call: the diagram source is POSTed to Kroki and the
image comes back in the response body. There is nothing to install and nothing
to authenticate — `--format svg`/`--format png` works out of the box against the
public `https://kroki.io` instance.

```
strata diagram show -f topology --format svg --save topology.svg
strata diagram show -f topology --format png --save topology.png
```

`--format` defaults to `mmd` (plain Mermaid text, unchanged from before). `svg`/`png`
always write to a file — binary image data isn't printed to the console. If `--save`
is omitted, the file defaults to `diagram.<format>` in the workspace root.

Self-hosting
Point strata at your own Kroki instance instead of the public one:

```
export STRATA_KROKI_ADDRESS=https://kroki.internal.example.com
```

Or declare it as a normal integration (this takes priority over the env var):

```yaml
integrations:
  - name: kroki
    type: kroki
    capabilities: [diagram_render]
    endpoints:
      address: https://kroki.internal.example.com
```

Self-hosting Kroki itself needs the core `yuzutech/kroki` image **plus** the
`yuzutech/kroki-mermaid` companion container — Mermaid isn't rendered by the core
server alone:

```
docker run -d -p 8000:8000 \
  -e KROKI_MERMAID_HOST=mermaid \
  yuzutech/kroki
docker run -d --name mermaid yuzutech/kroki-mermaid
```

See https://docs.kroki.io/kroki/setup/install/ for the full self-hosting guide
(Docker Compose, other companion containers, etc.).

Troubleshooting
- `Failed to reach Kroki at ...` — network/DNS issue reaching the configured
  address (public instance needs outbound HTTPS; self-hosted needs the companion
  container reachable from the core Kroki container).
- `Kroki returned HTTP 4xx/5xx ...` — the rendered Mermaid text itself was
  rejected; this usually means a genuine Mermaid syntax problem in the diagram
  definition, not a Kroki configuration issue.

See also: `strata help --topic integrations`, ADR-0034 (diagram visualization).
