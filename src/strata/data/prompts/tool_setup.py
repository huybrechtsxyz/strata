"""Built-in prompt for guided tool installation / configuration assistance."""

from __future__ import annotations

import os
from typing import Any


class ToolSetupPrompt:
    """Tailored, state-aware setup guide for a single integration."""

    VERSION = "1.0"

    SYSTEM = """\
You are a DevOps toolchain setup assistant for a strata infrastructure workspace.
An operator needs help installing or configuring a specific integration tool.
You have been given the current runtime state (installed vs. missing, env vars set or not)
and static metadata about the tool. Produce a JSON object only.

Required fields:
  "status_summary"  : 1-2 sentence plain-language description of the current state
                      (e.g. "terraform binary is not installed; no API token is configured").
  "already_done"    : list of steps the operator can skip because they are already satisfied
                      (e.g. "binary is installed", "TF_TOKEN is set").
  "steps"           : list of step objects, each with:
                        "order"    : integer (1-based),
                        "title"    : short action title,
                        "commands" : list of exact shell commands to run (OS-appropriate),
                        "notes"    : optional extra context or follow-up.
  "verify_command"  : the single command to confirm everything is working
                      (prefer: strata tools check <name>).
  "references"      : list of URLs for install docs, auth setup, etc. (may be empty).

Rules:
- Skip steps that are already satisfied (binary present, env var set, authenticated).
- Provide OS-appropriate commands (Windows: winget/PowerShell; macOS: brew; Linux: apt/curl).
- Never suggest storing secrets in files — use env vars or the workspace secret store.
- Never fabricate version numbers or API token values."""

    @staticmethod
    def build_user_prompt(
        tool_detail: dict[str, Any],
        os_name: str,
        workspace_context: dict[str, Any],
    ) -> str:
        name = tool_detail.get("name", "unknown")
        available = tool_detail.get("available", False)
        version = tool_detail.get("version") or "not installed"
        install_url = tool_detail.get("install_url") or ""
        command = tool_detail.get("command") or "SDK only"
        capabilities = tool_detail.get("capabilities") or []
        env_vars: list[dict[str, Any]] = tool_detail.get("env_vars") or []
        auth_methods: list[dict[str, Any]] = tool_detail.get("auth_methods") or []

        lines: list[str] = [
            f"Tool: {name}",
            f"OS: {os_name}",
            f"Status: {'available (version ' + version + ')' if available else 'NOT installed'}",
            f"CLI command: {command}",
        ]
        if install_url:
            lines.append(f"Install URL: {install_url}")
        if capabilities:
            lines.append(f"Capabilities: {', '.join(capabilities)}")

        # Env vars with live is_set state
        if env_vars:
            lines.append("")
            lines.append("Environment variables:")
            for ev in env_vars:
                is_set = bool(os.environ.get(ev["name"]))
                req = "required" if ev.get("required") else "optional"
                set_str = "SET" if is_set else "NOT SET"
                purpose = ev.get("purpose", "")
                lines.append(f"  {ev['name']} ({req}, {set_str}) — {purpose}")

        # Auth methods
        if auth_methods:
            lines.append("")
            lines.append("Available auth methods:")
            for am in auth_methods:
                lines.append(f"  - {am.get('method', '')}: {am.get('description', '')}")

        # Workspace context
        provisioner_types = workspace_context.get("provisioner_types") or []
        backend_types = workspace_context.get("backend_types") or []
        if provisioner_types or backend_types:
            lines.append("")
            lines.append("Workspace context:")
            if provisioner_types:
                lines.append(f"  Provisioner types in use: {', '.join(provisioner_types)}")
            if backend_types:
                lines.append(f"  Backend types: {', '.join(backend_types)}")

        return "\n".join(lines)
