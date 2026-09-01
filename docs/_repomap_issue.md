Title: Consolidate @repo_name/... reference detection into a single shared predicate

Body:
~15 call sites independently reimplement str.startswith("@") to detect a cross-repo @repo_name/... reference, instead of sharing one helper:

utils/graph.py:85, utils/system.py:204, services/base_service.py:552, services/template_resolver.py:410,450, services/module_service.py:55, services/deployment_service.py:1645, controllers/promote_controller.py:1332, controllers/guide_controller.py:148, controllers/graph_controller.py:407, controllers/diagram_source_controller.py:514,1089, builders/compose_builder.py:445, commands/deploy/run_deploy_command.py:380, commands/validate/run_validate_command.py:95
resolve_path() in utils/system.py already centralizes actual resolution, but the detection check is duplicated everywhere. Extract a shared is_repo_reference(path: str) -> bool next to resolve_path() and update all call sites to use it.

Not in scope: helm_deployer._TOKEN_RE, gate_controller._OPERATOR_RE, sbom_utils._SEMVER_RE, version_service._GIT_SHA_RE/_OCI_DIGEST_RE — different domains, correctly local to their files.