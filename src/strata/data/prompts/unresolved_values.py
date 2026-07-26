"""Built-in prompt for unresolved deployment values analysis."""

from __future__ import annotations

from typing import Any


class UnresolvedValuesPrompt:
    """Explain why values failed to resolve and suggest how to define them."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps configuration assistant for a strata infrastructure deployment.
One or more deployment values (variables, secrets, or feature flags) could not be resolved.
Analyse each failure and respond with a JSON object only.

Required fields:
  "summary"      : 1-2 sentence overview of the resolution failures.
  "entries"      : list of objects for each unresolved value, each with:
                     "key"          : the value key,
                     "type"         : "variable" | "secret" | "feature",
                     "store"        : store type (environment, azure_keyvault, bitwarden, etc.),
                     "likely_cause" : why this failed (env var not set, secret not found, etc.),
                     "fix"          : exact step to resolve — env var export, secret creation command, etc.
  "recommendations" : list of strings — broader advice (profile file refs, secret store setup, etc.).

Store-specific guidance:
  environment  → set the env var: export KEY=value  (or add to .strata profile env file)
  azure_keyvault → create secret: az keyvault secret set --vault-name <vault> --name <key> --value <val>
  bitwarden    → create secret: bws secret create <name> --note <value> --project-id <id>
  vault        → write secret: vault kv put secret/<path> <key>=<value>
  infisical    → add via Infisical dashboard or CLI
  constant     → set the literal value in the YAML declaration

Never fabricate secret values. Provide templates with placeholders where actual values are unknown."""

    @staticmethod
    def build_user_prompt(unresolved: list[dict[str, Any]], context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        env_file = context.get("env_file", "")

        lines = []
        for entry in unresolved:
            key = entry.get("key", "?")
            etype = entry.get("type", "?")
            store = entry.get("store", "?")
            store_ref = entry.get("store_ref", "")
            error = entry.get("error", "")
            line = f"  [{etype}] {key}  store={store}"
            if store_ref:
                line += f"  ref={store_ref}"
            if error:
                line += f"\n    error: {error}"
            lines.append(line)

        header = f"Deployment: {deployment}"
        if env_file:
            header += f"\nEnvironment file: {env_file}"

        return f"{header}\n\nUnresolved values ({len(unresolved)}):\n" + "\n".join(lines)
