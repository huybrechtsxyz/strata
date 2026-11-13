# How To: Write one deployment base for many spokes with parameter sets

A hub-and-spoke platform repeats the same deployment for every spoke: the same workspace, the
same environments, the same properties, with only a name, a subscription and a code changed.
Copying the deployment per spoke works until the copies drift. This guide writes the deployment
**once**, as a base that reads its values with `${param:<role>.<key>}`, and gives each spoke one
[parameter set](../config/parameterset.md) and one short leaf.

The solution below is complete: two hubs, two spokes and one customer project. Every file is shown
with its path. strata finds documents by `kind` and `meta.name`, never by folder, so the layout is
one that reads well, not one strata requires. A test builds this exact solution from this page and
validates it, so what you see here works.

## The layout

```
strata.yaml
platform/                       shared documents: providers, workspaces, shared environments
  provider.yaml
  workspace-spoke.yaml
  workspace-customer.yaml
  environment-solution.yaml
tenants/
  acme.yaml
  initech.yaml
stacks/                         one base per stack, with its defaults
  spoke/deployment.yaml
  spoke/defaults.yaml
  customer/deployment.yaml
hubs/
  hub01/hub.yaml                the hub's parameter set and environment
  hub01/environment.yaml
  hub01/acme-001/spoke.yaml     the spoke's parameter set
  hub01/acme-001/deploy-spoke.yaml
  hub01/acme-001/acmep01/customer.yaml
  hub01/acme-001/acmep01/deploy-customer.yaml
  hub02/hub.yaml
  hub02/environment.yaml
  hub02/initech-001/spoke.yaml
  hub02/initech-001/deploy-spoke.yaml
```

Everything about one spoke lives in its folder, and everything every spoke shares lives in
`stacks/`.

## 1. The shared documents

Nothing here is new. A solution, a provider, a workspace per stack, a shared environment and two
tenants:

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
# platform/workspace-spoke.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: spoke
spec:
  providers: [azure]
  provisioners:
    - name: tf
      tool: terraform
      source:
        source_path: infra/spoke
```

```yaml
# platform/workspace-customer.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: customer
spec:
  providers: [azure]
  provisioners:
    - name: tf
      tool: terraform
      source:
        source_path: infra/customer
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

```yaml
# tenants/acme.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: acme
spec:
  display_name: Acme
```

```yaml
# tenants/initech.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: initech
spec:
  display_name: Initech
```

## 2. One parameter set per hub, spoke and customer project

A parameter set is plain values. It does not say what it is for: the deployment that names it
gives it a **role** (`hub`, `spoke`, `customer`), and bases read `${param:<role>.<key>}`. So the
spoke's `code` and the customer's `code` never collide: one is `spoke.code`, the other
`customer.code`.

```yaml
# hubs/hub01/hub.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: hub-hub01
spec:
  values:
    name: hub01
```

```yaml
# hubs/hub02/hub.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: hub-hub02
spec:
  values:
    name: hub02
    region: westus2          # overrides the stack default below
```

```yaml
# hubs/hub01/acme-001/spoke.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: spoke-acme-001
spec:
  values:
    name: acme-001
    tenant: acme
    code: acme
    seq: "001"               # quoted: 001 unquoted is the number 1
    subscription_id: 00000000-0000-0000-0000-000000000001
```

```yaml
# hubs/hub02/initech-001/spoke.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: spoke-initech-001
spec:
  values:
    name: initech-001
    tenant: initech
    code: initech
    seq: "001"
    subscription_id: 00000000-0000-0000-0000-000000000002
```

```yaml
# hubs/hub01/acme-001/acmep01/customer.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: customer-acmep01
spec:
  values:
    code: acmep01
    project: p01
```

A parameter set holds no `${...}` tokens and no secrets: it is plain text in git. Keep
`${secret:}` in the document that needs the secret.

## 3. One base per stack

The spoke base is the whole spoke deployment, written once. Its own `params` names a set of
defaults for the `hub` role, like a chart's `values.yaml`: a hub's own set overrides them key by
key.

```yaml
# stacks/spoke/defaults.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: hub-defaults
spec:
  values:
    region: westeurope
```

```yaml
# stacks/spoke/deployment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke_deployment_base
  params: { hub: hub-defaults }
  name_rule: spoke-${param:spoke.name}
spec:
  partial: true
  workspace: spoke
  tenant: ${param:spoke.tenant}
  layers:
    segments:
      hub: ${param:hub.name}
      spoke: ${param:spoke.name}
  environments:
    - solution-env
    - hub-${param:hub.name}-env
  properties:
    region: ${param:hub.region}
    subscription_id: ${param:spoke.subscription_id}
    state_account: sttfstate${param:spoke.code}${param:spoke.seq}
```

- `${param:}` works in reference fields too: `tenant` and the environment `hub-${param:hub.name}-env`
  are checked as real names once each spoke's values are filled in.
- `name_rule` keeps names honest: every deployment built on this base must be named
  `spoke-<spoke name>`. Names stay plain text, so every command still finds a deployment by its
  name, but they cannot drift from the values.

The customer base reads three roles: the spoke it runs in, the hub, and its own project.

```yaml
# stacks/customer/deployment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: customer_deployment_base
  name_rule: customer-${param:spoke.name}-${param:customer.code}
spec:
  partial: true
  workspace: customer
  tenant: ${param:spoke.tenant}
  environments:
    - solution-env
    - hub-${param:hub.name}-env
  properties:
    project: ${param:customer.project}
    deploy_code: ${param:customer.code}
    subscription_id: ${param:spoke.subscription_id}
```

## 4. One short leaf per spoke and per project

A leaf names its sets per role and extends the base. That is all it holds, unless it has an
exception of its own.

```yaml
# hubs/hub01/acme-001/deploy-spoke.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke-acme-001
  params: { hub: hub-hub01, spoke: spoke-acme-001 }
spec:
  extends: spoke_deployment_base
```

```yaml
# hubs/hub02/initech-001/deploy-spoke.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke-initech-001
  params: { hub: hub-hub02, spoke: spoke-initech-001 }
spec:
  extends: spoke_deployment_base
```

```yaml
# hubs/hub01/acme-001/acmep01/deploy-customer.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: customer-acme-001-acmep01
  params: { hub: hub-hub01, spoke: spoke-acme-001, customer: customer-acmep01 }
spec:
  extends: customer_deployment_base
```

Every leaf behind `hub02` names `hub: hub-hub02`. A set never names the sets it builds on, and
nothing is implied by the folder: one name per leaf is the price of every value being explicit.

Each hub still has its own environment. An environment can also be built on a base that reads
parameter sets ([How To: tenant and environment bases](environment-and-tenant-bases.md)); with
one variable per hub, writing each out is shorter:

```yaml
# hubs/hub01/environment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: hub-hub01-env
spec:
  variables:
    - { key: hub_vnet, store: constant, type: string, value: vnet-hub01 }
```

```yaml
# hubs/hub02/environment.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: hub-hub02-env
spec:
  variables:
    - { key: hub_vnet, store: constant, type: string, value: vnet-hub02 }
```

## 5. Check it

```
strata validate
strata deploy list
```

`strata deploy list` shows each leaf as resolved (`spoke-initech-001` with tenant `initech`
and environments `solution-env, hub-hub02-env`) and each base as written, tokens and all. The
merged result for `spoke-initech-001`:

```yaml
spec:
  workspace: spoke
  tenant: initech
  layers: { segments: { hub: hub02, spoke: initech-001 } }
  environments: [solution-env, hub-hub02-env]
  properties:
    region: westus2                 # hub-hub02 overrides hub-defaults
    subscription_id: 00000000-0000-0000-0000-000000000002
    state_account: sttfstateinitech001
```

To see where each value comes from, render the deployment. Every field names the document that
wrote it; a value filled in from a parameter set shows the text as written and the set it read:

```
strata render deployment spoke-initech-001
```

```text
  deployment spoke-initech-001
  chain: spoke-initech-001 <- spoke_deployment_base
  parameter sets: hub: hub-defaults, hub-hub02; spoke: spoke-initech-001

  FIELD                          VALUE                FROM                   FILLED IN FROM
  spec.workspace                 spoke                spoke_deployment_base
  spec.environments[1]           hub-hub02-env        spoke_deployment_base  'hub-${param:hub.name}-env': hub.name = hub02 (hub-hub02)
  spec.tenant                    initech              spoke_deployment_base  '${param:spoke.tenant}': spoke.tenant = initech (spoke-initech-001)
  spec.properties.region         westus2              spoke_deployment_base  '${param:hub.region}': hub.region = westus2 (hub-hub02)
  spec.properties.state_account  sttfstateinitech001  spoke_deployment_base  'sttfstate${param:spoke.code}${param:spoke.seq}': spoke.code = initech (spoke-initech-001), spoke.seq = 001 (spoke-initech-001)
  ...
```

`strata render` also takes `environment` and `tenant`, and `-o json` for the full origin of
every field. Secret values are always masked.

A deployment built on a base runs like any other: `strata build run spoke-initech-001`. A base
does not run on its own.

## 6. When something is wrong

strata reports a problem on the file you wrote, says which values made it, and names the
deployment it was resolving:

- **A value is missing.** `${param:spoke.subscription}` in the base, but the sets hold
  `subscription_id`:
  `spoke_deployment_base spec.properties.subscription_id: ${param:spoke.subscription} not found in spoke: [spoke-acme-001] (for spoke-acme-001)`
- **A name does not exist once filled in.** A spoke set says `tenant: acmee`:
  `spoke_deployment_base spec.tenant: unknown tenant 'acmee' ... from '${param:spoke.tenant}': spoke.tenant = 'acmee' (spoke-acme-001) (for spoke-acme-001)`
- **A leaf has the wrong name.** `meta.name: spoke-acme` under the spoke base:
  `Deployment 'spoke-acme' must be named 'spoke-acme-001' by the name_rule of 'spoke_deployment_base'`
- **A value nothing reads** is information, not an error (`param_unused`): a set may hold more
  than one deployment needs. `strata validate --strict` still passes.

## Adding a spoke

Two files in a new folder, nothing else:

1. `hubs/hub02/umbrella-001/spoke.yaml`: a parameter set `spoke-umbrella-001` with the spoke's
   values.
2. `hubs/hub02/umbrella-001/deploy-spoke.yaml`: a leaf `spoke-umbrella-001` with
   `params: { hub: hub-hub02, spoke: spoke-umbrella-001 }` and `extends: spoke_deployment_base`.

`strata validate` then checks the new spoke's tenant, environments, name and values like every
other.

## Notes

- Quote a value that holds a token inside a YAML flow list or map:
  `environments: [solution-env, 'hub-${param:hub.name}-env']`. In block style, as above, no quotes
  are needed.
- `${param:}` is never allowed in `meta`, `spec.extends` or `spec.partial`: names, parameter sets
  and chains stay fixed.
- The YAML editor's schema (`.strata/schemas/`, refreshed by `strata sln update`) accepts a
  `${param:}` token wherever `strata validate` does, and still underlines one in `meta`,
  `extends` or `partial`. Whether the filled-in value is right, only `strata validate` can tell.
- The full rules are in [parameterset.md](../config/parameterset.md); the decision is
  [ADR-0061](../decisions/0061-parameter-sets-and-load-time-param-token.md).
