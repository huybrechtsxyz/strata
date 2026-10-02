# How To: Generate Repeated Per-Instance Terraform Blocks (Providers/Modules) From One Composite Value

You have many instances of the same pattern — e.g. one `provider "azurerm"` alias + one
`module` block per customer, each needing its own subscription/provider configuration — and
Terraform's own language rules won't let you collapse them into a single `for_each`/`count`
module call:

> Since the association between resources and provider configurations is static, module calls
> using `for_each` or `count` cannot pass different provider configurations to different
> instances. If you need different instances of your module to use different provider
> configurations then you must use a separate `module` block for each distinct set of provider
> configurations.

That's a real, permanent HCL-language constraint — strata cannot and should not try to route
around it. What strata *can* do is stop you from hand-maintaining `provider-1.tf`,
`provider-2.tf`, ... by hand: generate the repeated boilerplate from one template and one
composite list.

## The short answer

**Use `provisioner.output.template`** (ADR-0023 D3, rendered at `deploy run` time — ADR-0027) —
a real, already-shipped Jinja2 full-file-template escape hatch. Its render context already
includes `properties` (and `custom`), which is exactly the same merged dict described in
[composite-variable-fragments.md](composite-variable-fragments.md) — so if your per-customer
data is already assembled there, a loop over it is all the template needs:

```python
# strata/integrations/capabilities.py: render_output_template()'s real context
context = {
    "graph": graph,
    "variables": {...}, "flags": {...}, "secrets": {...},
    "properties": graph.properties,   # <- the merged, composite dict
    "custom": graph.custom,
    "provisioner": provisioner,
}
```

## Worked example

**The composite data** — assembled exactly as in
[composite-variable-fragments.md](composite-variable-fragments.md), one Environment document per
customer, each contributing its own fragment under `spec.properties`:

```yaml
spec:
  properties:
    agw_customers:
      unisonplanning:
        subscription_id: "11111111-1111-1111-1111-111111111111"
        domain: "unisonplanning.com"
      acmecorp:
        subscription_id: "22222222-2222-2222-2222-222222222222"
        domain: "acmecorp.com"
```

**The template** — `providers.tf.j2`, referenced from the provisioner:

```jinja
{% for customer, cfg in properties.agw_customers.items() %}
provider "azurerm" {
  alias           = "{{ customer }}"
  subscription_id = "{{ cfg.subscription_id }}"
  features {}
}

module "agw_{{ customer }}" {
  source = "../../modules/agw"
  providers = {
    azurerm = azurerm.{{ customer }}
  }
  domain = "{{ cfg.domain }}"
}
{% endfor %}
```

**The provisioner** declares it:

```yaml
spec:
  provisioners:
    - name: tf_main
      tool: terraform
      source:
        source_path: infra
      output:
        template: providers.tf.j2
```

**The result**, written once per `strata deploy run`, as `providers.tf` (the `.j2` suffix is
stripped — `template_path.name` with `.j2`/`.jinja2`/`.jinja` removed): one real, static,
Terraform-legal `provider`/`module` block pair per customer, generated — never hand-copied.
Onboarding a fourth customer means adding one more fragment file (per
[composite-variable-fragments.md](composite-variable-fragments.md)) — the template and the
provisioner declaration never change.

## Rules that actually matter

- **Build-time only validates; deploy-time actually renders.** `strata build run` checks that
  every name the template references (`properties`, `variables`, etc.) exists somewhere in the
  declared schema (`validate_template_references()`) — it never renders, and never catches a
  Jinja2 loop producing invalid HCL. The real render — and the first point anything resembling a
  Terraform syntax error would surface — happens at `strata deploy run`, via
  `InfraIntegration.render_output_template()`.
- **This is the sanctioned "generate a new file" escape hatch, not a source rewrite.** Unlike
  `sync_source()` (which copies vendored/third-party source byte-for-byte, deliberately *never*
  template-rendered — ADR-0025), `output.template` produces a file your own deployment owns
  outright. Nothing about this violates strata staying opaque to AGW/WAF/whatever-shape your
  template emits — the template's author owns the HCL shape completely; strata only ever
  substitutes values into it.
- **`properties`/`custom` are intentionally "unchecked, arbitrary-shape" roots** in
  `validate_template_references()` — only the top-level name (`properties`) is checked to exist,
  never its nested keys (`properties.agw_customers.unisonplanning...`). A typo inside a fragment,
  or inside the loop body, is invisible until the real `deploy run` render — same opacity
  tradeoff as everywhere else `properties` is used.
- **One `output.template` per provisioner.** If a provisioner needs both the default
  `*.auto.tfvars.json` projection *and* a generated file like this, check whether your case
  actually needs the default projection at all — `output.template` **replaces**
  `default_output()`'s projection entirely for that provisioner, it does not layer on top of it.

## Related

- [composite-variable-fragments.md](composite-variable-fragments.md) — how the composite
  `properties` value this template loops over gets assembled from several files in the first
  place.
- [docs/design/composite-variable-merge.md](../design/composite-variable-merge.md) — the full
  investigation this pattern grew out of.
- [docs/design/value-token-resolution.md](../design/value-token-resolution.md) — the full
  `output.template` design (build-time validation vs. deploy-time render split).
- [docs/decisions/0023-build-output-rendering.md](../decisions/0023-build-output-rendering.md) /
  [0027-strata-deploy-run.md](../decisions/0027-strata-deploy-run.md) — where `output.template`
  was designed and where its actual render was implemented.
