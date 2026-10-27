Security gates (plan)
10	opa_policy	Only if a consumer actually needs it

Open from the earlier review:

#7: issue links are always empty.
#10: design §6.3 names functions that don't exist.

Design questions:

Should strata warn when a pinned commit differs from CI's commit?
Should strata validate check gate credentials?
An image that matches several Aikido containers always fails; keep that?

Before the design moves to design: Phase 7, the definition-of-done checklist (including ≥90% test coverage on security_gate_controller.py and aikido.py), then archive the plan and the vendor reference.

Integration readiness (doc)
Phases 1-8 and review fixes R1-R11 are done.
No ADR yet; it probably needs two (the readiness preflight, and declarable local-tool types).

Other open items
~~Six copies of the deployment-to-workspace lookup still don't raise an error when the lookup fails.~~ Done 2026-10-27: they skip on purpose (validation, audit manifest); now one non-raising `deployment_workspace()` in `deployment_resolution.py`, which `resolve_deployment_workspace()` also uses.
Compare Aikido's repo scan with strata's Checkov on the build/ output for haven prd.
Safe Chain settings in consumer pipelines (the 48-hour package-age exclusion and the private feed).
Registering Azure DevOps repositories in Aikido.
Whether Device Protection flags a sideloaded strata .vsix.
Aikido pricing tier and data residency.
Release 2.0.0-RC2. Its notes need to call out the 9b breaking change for open-source Checkov users.
A CI recipe: build run, then policy check, then deploy run.
VS Code "Integrations" and "Policies" views.
Delete the stray .mypy_cache.
~~This machine's checkov.cmd is broken.~~ Done 2026-10-27: reinstall alone does not fix it (checkov's own `checkov.cmd` needs `python.exe` on PATH or a `.py` association); with the uv tool's `Scripts` folder on PATH, 9b confirmed against real checkov 3.3.30, and `scanner_version` now comes from the scan JSON.

