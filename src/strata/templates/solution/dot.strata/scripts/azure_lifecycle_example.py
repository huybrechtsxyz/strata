"""Azure lifecycle script starter — copy and adapt for your deployment.

Place this file in .strata/scripts/ and reference it from your workspace YAML
lifecycle block.  Rename it to describe what it does (e.g. pre_deploy_aks.py).

Quick start — reference built-in scripts directly, no code needed:
===============================================================

    lifecycle:
      pre_deploy:
        scripts:
          # Fetch AKS credentials before Helm / ArgoCD deploy
          - ${STRATA_AZURE_SCRIPTS}/azure_aks_credentials.py

          # Log Docker into Azure Container Registry
          - ${STRATA_AZURE_SCRIPTS}/azure_acr_login.py

      pre_provision:
        scripts:
          # Create resource group if it doesn't exist (Bicep subscription-scope)
          - ${STRATA_AZURE_SCRIPTS}/azure_resource_group_ensure.py

    variables:
      - key: AKS_CLUSTER
        source: constant
        value: my-aks-cluster
      - key: AKS_RESOURCE_GROUP
        source: constant
        value: my-resource-group
      - key: ACR_NAME
        source: constant
        value: myregistry
      - key: AZURE_RESOURCE_GROUP
        source: constant
        value: my-rg-prod
      - key: AZURE_LOCATION
        source: constant
        value: westeurope


Custom script — subclass AzureScript:
======================================

Rename this file, delete the comment block above, and implement your logic:

    from strata.utils.azure_script_base import AzureScript

    class MyDeployScript(AzureScript):
        def run(self):
            # Read variables injected by strata into the environment
            cluster = self.require_env("AKS_CLUSTER")       # exits if missing
            rg = self.require_env("AKS_RESOURCE_GROUP")

            # Run any az command
            result = self.run_az([
                "aks", "get-credentials",
                "--resource-group", rg,
                "--name", cluster,
                "--overwrite-existing",
            ])

            # Fail the lifecycle step if az returns non-zero
            self.exit_on_failure(result, "az aks get-credentials")

            # Log status (appears in strata console output)
            self.log(f"AKS credentials fetched for {cluster}")

    if __name__ == "__main__":
        MyDeployScript().execute()


Available helpers on AzureScript:
===================================

    self.run_az(args)              Run az subcommand; returns CompletedProcess
    self.exit_on_failure(result)   sys.exit(1) if returncode != 0
    self.require_env("VAR")        Get env var or exit(1) with clear error
    self.env("VAR", default="")    Get env var with optional default
    self.get_token(resource)       Get cached bearer token (ARM, KeyVault, etc.)
    self.workspace_path()          Path from STRATA_WORKSPACE_PATH
    self.build_path()              Path from STRATA_BUILD_PATH
    self.stage_name()              Stage name from STRATA_STAGE_NAME
    self.log("message")            Print to stderr (visible in strata output)

STRATA_* environment variables available in all lifecycle scripts:
    STRATA_PHASE, STRATA_WORKSPACE_PATH, STRATA_BUILD_PATH,
    STRATA_CONFIG_PATH, STRATA_OBJECT_PATH, STRATA_STAGE_NAME
    + all resolved secrets and variables from the active deployment.

Docs: strata help --topic azure_cli
"""

# Uncomment and adapt the block below to create your custom script:
#
# import sys
# sys.path.insert(0, '')
# from strata.utils.azure_script_base import AzureScript
#
# class MyScript(AzureScript):
#     def run(self):
#         result = self.run_az(["account", "show"])
#         self.exit_on_failure(result, "az account show")
#         self.log("Azure CLI is authenticated")
#
# if __name__ == "__main__":
#     MyScript().execute()
