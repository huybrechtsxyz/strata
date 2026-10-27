Security gates (plan)
10	opa_policy	Only if a consumer actually needs it

Design questions:

~~Should strata warn when a pinned commit differs from CI's commit?~~ Done 2026-10-27 (design D6a): `release_gate_commit_mismatch`, a warning under `warn`, an error under `deny`; `--commit` is not compared.
~~Should strata validate check gate credentials?~~ No (2026-10-27, design R14): validate stays offline; `integration check --deployment` and `policy check` cover it.
An image that matches several Aikido containers always fails; keep that?

Before the design moves to design: Phase 7, the definition-of-done checklist (including ≥90% test coverage on security_gate_controller.py and aikido.py), then archive the plan and the vendor reference.

Integration readiness (doc)
Phases 1-8 and review fixes R1-R11 are done.
No ADR yet; it probably needs two (the readiness preflight, and declarable local-tool types).

Other open items
Release 2.0.0-RC2. Its notes need to call out the 9b breaking change for open-source Checkov users.
A CI recipe: build run, then policy check, then deploy run.
VS Code "Integrations" and "Policies" views.

