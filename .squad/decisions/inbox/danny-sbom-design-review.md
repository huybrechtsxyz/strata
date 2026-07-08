### 2026-06-12: SBOM collector design review
**By:** Danny
**Verdict:** APPROVED WITH CONDITIONS
**Rationale:**

The collector pattern is architecturally sound — sub-components of a builder, not independent services or integrations, with CycloneDX isolated to `SbomBuilder` only. Composition over inheritance is correctly applied. The design fits the existing `BaseBuilder` surface without introducing new layers.

One item would have been a REJECTION if not caught here:

**BLOCKER resolved by constraint #1 below:** The design places `sbom.json` at `{work_path}/.strata/sbom.json`. This directly violates the 2026-05-21 active decision: "`.strata/` is internal CLI state. Build artifacts go to `work_path/build/`." The file must live inside the versioned build directory alongside `platform.json`.

---

**Implementation constraints for Linus:**

1. **sbom.json goes into the build directory, not `.strata/`.**
   Write to `{build_path}/{deployment_name}/sbom.json` (same layout as `platform.json`). `SbomReferenceModel.path` stores a workspace-relative path from `work_path`. The `clean_build_command.py` change can be dropped entirely — `shutil.rmtree(instance_path)` already cleans the entire deployment build directory.

2. **`SbomComponentModel` and `SbomReferenceModel` must extend `PlatformBaseModel`, not bare `BaseModel`.**
   All internal strata models use `PlatformBaseModel` (which sets `extra="forbid"`). No exception for SBOM models. Check `ManifestArtifactImageModel` for the precedent.

3. **`SbomBuilder.build()` must accept an optional `platform_model: Optional[PlatformArtifactModel]` parameter.**
   This mirrors `TerraformBuilder.build()`. Without it, dry-run (`--dry-run`) cannot work because `platform.json` has not been written yet when `_execute_sbom_build()` runs. Load from disk only when `platform_model is None`.

4. **Drain collector warnings into `self._messages` immediately after each `collect()` call.**
   Collectors use `get_warnings()` — a pattern not shared by `BaseBuilder`. In `SbomBuilder.build()`, after each `collector.collect(...)`, call `self._messages.extend(collector.get_warnings())`. Do not let warnings accumulate silently until after all collectors finish.

5. **`strata build sbom` must be a `SbomBuildCommand(BaseBuildCommand)` class in `commands/builders/`.**
   Do not wire it inline in `cli_builders.py`. Follow the `RunBuildCommand` / `CleanBuildCommand` pattern: a dedicated class file, `execute()` method, `get_required_integrations()`, proper exit code via `handle_command_exit`. The Click function in `cli_builders.py` is a thin wrapper only.

6. **Do not duplicate `AnsibleDeployer._get_requirements_file()` logic in `AnsibleCollector`.**
   If the discovery logic is identical, move it to `utils/` or expose it from `AnsibleDeployer` as a static/class method. Two copies of path-discovery logic for the same convention is a maintenance trap.

7. **`cyclonedx-python-lib>=7.0,<9` is acceptable short-term, but pin to `<8` if the 7.x → 8.x API break is known.**
   Do not leave a wide open upper bound if the library has breaking major versions. Check the changelog before committing the version range.

8. **`sbom_utils.py` must have zero imports from `builders/` or `services/`.**
   It is a `utils/` module. Pure functions, no side effects, no service references. PURL assembly and floating-tag detection are pure string/pattern operations — keep them that way.

9. **`deployment_manifest_model.py`: change `sbom` field type to `Optional[SbomReferenceModel]`, import from `models/sbom_model.py`.**
   The existing `Optional[Dict[str, Any]]` stub is a placeholder exactly for this. The field description must also be updated (remove "future").

10. **`SbomBuilder` is step 6 (after `_execute_helm_build()`), but must not block the overall build on CycloneDX serialization failure.**
    If CycloneDX BOM generation fails (e.g. library version mismatch, unexpected component type), log the error, append to `self._errors`, and return `False` from `build()`. Do not swallow it or emit only a warning — an SBOM that cannot be written is a build failure, not a soft warning.
