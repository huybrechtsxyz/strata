### 2026-06-12T00:00:00Z: SBOM integration validation
**By:** Basher

#### CycloneDX API
- Installed version: **NOT INSTALLED** — `cyclonedx-python-lib` is absent from `pyproject.toml` and from `.venv`. `packageurl-python` is also absent.
- Correct imports (verified against v7.0.0 source at `github.com/CycloneDX/cyclonedx-python-lib`):
  - `from cyclonedx.model.bom import Bom` ✓
  - `from cyclonedx.model.component import Component, ComponentType` ✓
  - `from cyclonedx.model.property import Property` ✗ **WRONG** — `Property` is defined in `cyclonedx/model/__init__.py`, correct import is `from cyclonedx.model import Property`
  - `from cyclonedx.output.json import JsonV1Dot6` ✓
  - `from packageurl import PackageURL` ✓ (from `packageurl-python` package)
- Component constructor (keyword-only args, verified from v7 source):
  ```python
  Component(
      name: str,
      type: ComponentType = ComponentType.LIBRARY,
      version: Optional[str] = None,
      purl: Optional[PackageURL] = None,
      properties: Optional[Iterable[Property]] = None,
      group: Optional[str] = None,
      description: Optional[str] = None,
      # ... many more optional fields
  )
  ```
- Serialization: `JsonV1Dot6(bom).output_as_string(indent=None)` ✓ confirmed — `generate()` is called internally, `bom.validate()` runs inside it (requires no extra validation extras unless schema validation is needed).
- Issues found:
  1. **BLOCKER**: `cyclonedx-python-lib` and `packageurl-python` must be added to `pyproject.toml` `[project.dependencies]`. Suggested pin: `cyclonedx-python-lib>=7.0,<9`, `packageurl-python>=0.11,<2`.
  2. **BLOCKER**: Wrong `Property` import path — `from cyclonedx.model.property import Property` does not exist in v7/v8. Use `from cyclonedx.model import Property`.

#### python-hcl2 provider parsing
- API: `import hcl2; data = hcl2.load(file_obj)` or `hcl2.loads(string)` — confirmed working against real `.tf` file with `python-hcl2==8.1.2`.
- Dict structure from parsing (confirmed empirically):
  ```python
  {
    "terraform": [
      {
        "required_providers": [
          {
            "hcloud": {"source": "\"hetznercloud/hcloud\"", "version": "\"~> 1.49\""},
            "azurerm": {"source": "\"hashicorp/azurerm\"", "version": "\"~> 3.90\""},
            "__is_block__": True
          }
        ],
        "__is_block__": True
      }
    ]
  }
  ```
- Key path for `required_providers`: `data["terraform"][0]["required_providers"][0]`
- **CRITICAL QUIRK**: All HCL string values come back with embedded surrounding quotes. `source` is `'"hetznercloud/hcloud"'` not `'hetznercloud/hcloud'`. Callers must strip: `value.strip('"')`.
- Iteration pattern:
  ```python
  rp = data["terraform"][0]["required_providers"][0]
  for provider_name, cfg in rp.items():
      if provider_name == "__is_block__":
          continue
      source = cfg["source"].strip('"')
      version = cfg["version"].strip('"')
  ```
- Multiple `terraform {}` blocks (unusual but valid) would appear as additional elements in `data["terraform"]` list — caller should iterate over all.

#### Ansible requirements.yml
- Structure: no `requirements.yml` files exist in `config/` or anywhere in the workspace. Validation is based on the Ansible Galaxy standard and the deployer's own handling in `ansible_deployer.py` (`_get_requirements_file` looks for `requirements.yml` or `collections/requirements.yml`).
- Standard structure (both keys optional):
  ```yaml
  collections:
    - name: community.general       # {namespace}.{collection}
      version: "7.0.0"
    - name: ansible.posix
      version: "1.5.4"
  roles:
    - name: geerlingguy.docker      # {author}.{role} — Galaxy convention
      version: "6.0.0"
  ```
- PURL for collections: `pkg:ansible/community.general@7.0.0` — proposed `pkg:ansible/{namespace}.{collection}@{version}` is correct for the community convention (no official PURL type for ansible in the purl-spec).
- PURL for roles: `pkg:ansible/geerlingguy/docker@6.0.0` — proposed `pkg:ansible/{author}/{role}@{version}` diverges from the community convention where roles use `{author}.{role}` dot notation (same as Galaxy install name). Recommend `pkg:ansible/geerlingguy.docker@6.0.0` for consistency, or use `pkg:github/geerlingguy/ansible-role-docker@6.0.0` if the source repo is known. Flag as undecided — there is no canonical standard.
- Flat-list style (no `collections`/`roles` keys, just a list) is also accepted by Galaxy but is less common and should not be assumed.

#### Overall verdict: ISSUES FOUND

Two blockers before implementation can proceed:

1. **Missing dependencies**: Add to `pyproject.toml`:
   ```toml
   "cyclonedx-python-lib>=7.0,<9",
   "packageurl-python>=0.11,<2",
   ```

2. **Wrong `Property` import**: Change `from cyclonedx.model.property import Property` → `from cyclonedx.model import Property`.

One non-blocking data-handling issue:

3. **python-hcl2 quote stripping**: All string values from `hcl2.load()` include surrounding double-quote characters and must be stripped with `.strip('"')` before use.

One open question (low priority):

4. **Ansible role PURL scheme**: No canonical standard. Decide between `pkg:ansible/{author}.{role}@{version}` (dot, Galaxy-style) vs `pkg:ansible/{author}/{role}@{version}` (slash, as proposed). Recommend dot-notation to match Galaxy install names.
