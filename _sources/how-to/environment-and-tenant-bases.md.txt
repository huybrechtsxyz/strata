# How To: Give every customer a tenant and an environment from shared bases

Every customer of a platform gets the same tenant document and the same environment: a display
name, a DNS slug, a tier, a feature switch, each changed per customer. Copying them per customer
works until the copies drift. This guide writes the tenant and the environment **once**, as bases
that read their values with `${param:<role>.<key>}`, and gives each customer one
[parameter set](../config/parameterset.md) and three short leaves.

It is the environment and tenant side of
[Write one deployment base for many spokes](parameterised-deployment-bases.md): the same
parameter sets, roles and `extends`, on the two kinds every deployment reads.

The solution below is complete, and a test builds it from this page and validates it. strata
finds documents by `kind` and `meta.name`, never by folder, so the layout is one that reads well,
not one strata requires.

## The layout

```
strata.yaml
platform/
  provider.yaml
  workspace.yaml
  environment-solution.yaml
customers/
  _shared/defaults.yaml         the defaults every customer starts from
  _shared/tenant.yaml           the tenant base
  _shared/environment.yaml      the environment base
  acme/customer.yaml            the customer's parameter set
  acme/tenant.yaml
  acme/environment.yaml
  acme/deploy.yaml
  initech/customer.yaml
  initech/tenant.yaml
  initech/environment.yaml
  initech/deploy.yaml
```

## 1. The shared documents

```yaml
# strata.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: platform
spec: {}
```

```yaml
# platform/provider.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure
spec:
  type: azure
  region: westeurope
```

```yaml
# platform/workspace.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: app
spec:
  providers: [azure]
  provisioners:
    - name: tf
      tool: terraform
      source:
        source_path: infra/app
```

```yaml
# platform/environment-solution.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: solution-env
spec:
  variables:
    - { key: owner, store: constant, type: string, value: platform-team }
```

## 2. The bases and their defaults

The defaults are a parameter set like any other. Both bases name it under the `customer` role,
so a customer's own set only lists what differs:

```yaml
# customers/_shared/defaults.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: customer-defaults
spec:
  values:
    tier: standard
    sso: false
```

The tenant base. `display_name` is required on a tenant, but a `partial: true` base may leave it
to the values. The tenant lists its customer's environment by name, built from the code:

```yaml
# customers/_shared/tenant.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: customer_tenant_base
  name_rule: ${param:customer.code}
  params: { customer: customer-defaults }
spec:
  partial: true
  display_name: ${param:customer.display_name}
  environments:
    - customer-${param:customer.code}-env
  properties:
    tier: ${param:customer.tier}
  configuration:
    dns_slug: ${param:customer.code}
```

The environment base. A token that is the whole value keeps its type, so `enable_sso` is the
boolean from the set, not the text `"false"`:

```yaml
# customers/_shared/environment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: customer_env_base
  name_rule: customer-${param:customer.code}-env
  params: { customer: customer-defaults }
spec:
  partial: true
  properties:
    customer_code: ${param:customer.code}
  variables:
    - key: dns_zone
      store: constant
      type: string
      value: ${param:customer.code}.example.com
  features:
    - key: enable_sso
      store: constant
      value: ${param:customer.sso}
```

`name_rule` keeps every leaf's name in line with its values: the tenant built for code `acme` must
be named `acme`, its environment `customer-acme-env`.

## 3. One folder per customer

A parameter set with what is the customer's own, and three leaves that name it:

```yaml
# customers/acme/customer.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: customer-acme
spec:
  values:
    code: acme
    display_name: Acme Corporation
```

```yaml
# customers/acme/tenant.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: acme
  params: { customer: customer-acme }
spec:
  extends: customer_tenant_base
```

```yaml
# customers/acme/environment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: customer-acme-env
  params: { customer: customer-acme }
spec:
  extends: customer_env_base
```

```yaml
# customers/acme/deploy.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app-acme
spec:
  workspace: app
  tenant: acme
  environments: [solution-env]
```

Initech overrides two defaults in its set, and its tenant adds one exception of its own, merged
over the base's `configuration`:

```yaml
# customers/initech/customer.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: customer-initech
spec:
  values:
    code: initech
    display_name: Initech
    tier: premium
    sso: true
```

```yaml
# customers/initech/tenant.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: initech
  params: { customer: customer-initech }
spec:
  extends: customer_tenant_base
  configuration:
    crm_id: "42"
```

```yaml
# customers/initech/environment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: customer-initech-env
  params: { customer: customer-initech }
spec:
  extends: customer_env_base
```

```yaml
# customers/initech/deploy.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app-initech
spec:
  workspace: app
  tenant: initech
  environments: [solution-env]
```

The deployments name only the tenant. The tenant brings its environment (`customer-initech-env`)
and its properties, merged in before the deployment's own (the tenant defaults).

## 4. Check it

```
strata validate
strata render tenant initech
```

strata merges each tenant and environment when it loads the solution, so every command sees the
merged documents. `strata render` shows one of them field by field, with the document that wrote
each value and the parameter set a filled-in value came from:

```text
  tenant initech
  chain: initech <- customer_tenant_base
  parameter sets: customer: customer-defaults, customer-initech

  FIELD                        VALUE                 FROM                  FILLED IN FROM
  spec.display_name            Initech               customer_tenant_base  '${param:customer.display_name}': customer.display_name = Initech (customer-initech)
  spec.environments[0]         customer-initech-env  customer_tenant_base  'customer-${param:customer.code}-env': customer.code = initech (customer-initech)
  spec.properties.tier         premium               customer_tenant_base  '${param:customer.tier}': customer.tier = premium (customer-initech)
  spec.configuration.dns_slug  initech               customer_tenant_base  '${param:customer.code}': customer.code = initech (customer-initech)
  spec.configuration.crm_id    42                    initech
```

`strata render environment customer-acme-env` shows `enable_sso` as `false`, from
`customer-defaults`; `strata render deployment app-initech` shows `tier: premium` coming from the
tenant base.

## 5. When something is wrong

- **A base is used directly.** A deployment that lists `customer_env_base` in `environments` is
  `environment_not_listable`; one whose `tenant` is `customer_tenant_base` is
  `tenant_not_selectable`. List or name a document built on the base.
- **A value is missing.** The base reads `${param:customer.code}` and a customer's set has no
  `code`: `customer_env_base spec.properties.customer_code: ${param:customer.code} not found in
  customer: [customer-defaults, customer-x] (for customer-x-env)`, reported when the solution
  loads.
- **A leaf has the wrong name.** `meta.name: initech-env` under the environment base:
  `Environment 'initech-env' must be named 'customer-initech-env' by the name_rule of
  'customer_env_base'`.

## Adding a customer

One folder with four files: the customer's parameter set, and a tenant, an environment and a
deployment that name it and extend the bases (or use the customer's tenant from an existing
deployment). `strata validate` checks the new names, values and references like every other.

## Notes

- A store entry of an environment (`variables`, `secrets`, `features`) is replaced whole by a
  child entry with the same `key`, never merged field by field. A tenant child's `geographies`
  replaces the base's list; it is never joined, since it is a data-residency limit.
- A secret's `value` never reads a parameter set: a set is plain text in git. Keep the secret in
  its store and write its name or reference in the value.
- `meta` is never templated or inherited; `extends` and `partial` never hold a token.
- `strata validate` reports a value no document reads as information (`param_unused`), not as an
  error; `--strict` still passes.
