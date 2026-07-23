"""Built-in strata script: authenticate to Azure Container Registry (ACR).

Runs ``az acr login --name <registry>`` so that subsequent docker push/pull
commands in the same stage can access the registry without additional auth.

Required environment variables:
    ACR_NAME    — ACR registry name (without .azurecr.io suffix)

Optional:
    ACR_SUBSCRIPTION  — Subscription ID (uses active subscription if absent)
    ACR_EXPOSE_TOKEN  — Set to "true" to also print the ACR access token to stdout

Usage in workspace YAML:
    lifecycle:
      pre_deploy:
        scripts:
          - <strata_data>/scripts/azure_acr_login.py

    variables:
      - key: ACR_NAME
        source: constant
        value: myregistry
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.azure_script_base import AzureScript


class AcrLogin(AzureScript):
    """Authenticate Docker to Azure Container Registry."""

    def run(self) -> None:
        acr_name = self.require_env("ACR_NAME")
        subscription = self.env("ACR_SUBSCRIPTION")
        expose_token = self.env("ACR_EXPOSE_TOKEN", "false").lower() == "true"

        args = ["acr", "login", "--name", acr_name]
        if subscription:
            args += ["--subscription", subscription]
        if expose_token:
            args.append("--expose-token")

        self.log(f"Logging into ACR registry '{acr_name}.azurecr.io'")
        result = self.run_az(args)
        self.exit_on_failure(result, "az acr login")
        self.log(f"ACR login successful ({acr_name}.azurecr.io)")


if __name__ == "__main__":
    AcrLogin().execute()
