# {{ name }} — Onboarding Checklist

- [ ] Review `{{ name }}.yaml` — verify code, zones, tier
- [ ] Set `onboarded` date in `{{ name }}.yaml`
- [ ] Configure dev provider overrides in `environments/dev.yaml`
- [ ] Configure qa provider overrides in `environments/qa.yaml`
- [ ] Configure prd provider overrides in `environments/prd.yaml`
- [ ] Add secrets and variable references under `references:`
- [ ] Validate: `strata validate tenants/{{ name }}/{{ name }}.yaml`
