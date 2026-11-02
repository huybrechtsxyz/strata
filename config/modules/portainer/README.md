# Stand-in for haven's real `services/hearth/portainer` source directory —
# not a strata document (discovery skips anything without a strata
# apiVersion, same as ../docker-compose.yaml). Exists so
# sync_module_source() has something real to copy for modules/portainer.yaml
# (source.remote: bundled, source.source_path: portainer) — the module's
# own docker-compose.yml is generated separately, merged with authentik's
# by ComposeIntegration.prepare_namespace() at the namespace level.
