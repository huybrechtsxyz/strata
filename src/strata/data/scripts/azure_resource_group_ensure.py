"""Built-in strata script: ensure an Azure resource group exists.

Runs ``az group create`` — Azure's create-or-update semantics mean this is
idempotent: if the resource group already exists with the same location the
command succeeds and makes no changes.

Required environment variables:
    AZURE_RESOURCE_GROUP    — Resource group name
    AZURE_LOCATION          — Azure region (e.g. westeurope, eastus)

Optional:
    AZURE_SUBSCRIPTION      — Subscription ID (uses active subscription if absent)
    AZURE_RG_TAGS           — Comma-separated key=value pairs to tag the RG
                              (e.g. "env=prod,team=platform")

Usage in workspace YAML (typically before a Bicep subscription-scope deployment):
    lifecycle:
      pre_provision:
        scripts:
          - <strata_data>/scripts/azure_resource_group_ensure.py

    variables:
      - key: AZURE_RESOURCE_GROUP
        source: constant
        value: my-rg-prod
      - key: AZURE_LOCATION
        source: constant
        value: westeurope
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.azure_script_base import AzureScript


class ResourceGroupEnsure(AzureScript):
    """Create an Azure resource group if it does not already exist."""

    def run(self) -> None:
        rg = self.require_env("AZURE_RESOURCE_GROUP")
        location = self.require_env("AZURE_LOCATION")
        subscription = self.env("AZURE_SUBSCRIPTION")
        tags_raw = self.env("AZURE_RG_TAGS")

        args = [
            "group",
            "create",
            "--name",
            rg,
            "--location",
            location,
            "--output",
            "json",
        ]
        if subscription:
            args += ["--subscription", subscription]
        if tags_raw:
            # "env=prod,team=platform" → ["env=prod", "team=platform"]
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            if tags:
                args += ["--tags"] + tags

        self.log(f"Ensuring resource group '{rg}' exists in '{location}'")
        result = self.run_az(args)
        self.exit_on_failure(result, "az group create")

        import json

        try:
            data = json.loads(result.stdout)
            status = data.get("properties", {}).get("provisioningState", "succeeded")
            self.log(f"Resource group '{rg}' ready (provisioningState: {status})")
        except Exception:
            self.log(f"Resource group '{rg}' ready")


if __name__ == "__main__":
    ResourceGroupEnsure().execute()
