"""AWS lifecycle script starter — copy and adapt for your deployment.

Quick start — reference built-in scripts directly, no code needed:
===============================================================

    lifecycle:
      pre_deploy:
        scripts:
          # Fetch EKS credentials before Helm / ArgoCD deploy
          - ${STRATA_AWS_SCRIPTS}/aws_eks_credentials.py

          # Log Docker into Amazon ECR
          - ${STRATA_AWS_SCRIPTS}/aws_ecr_login.py

      pre_provision:
        scripts:
          # Create S3 bucket for Terraform remote state (idempotent)
          - ${STRATA_AWS_SCRIPTS}/aws_s3_bucket_ensure.py

    variables:
      - key: EKS_CLUSTER
        source: constant
        value: my-eks-cluster
      - key: AWS_DEFAULT_REGION
        source: constant
        value: us-east-1
      - key: ECR_REGISTRY
        source: constant
        value: 123456789012.dkr.ecr.us-east-1.amazonaws.com
      - key: S3_BUCKET
        source: constant
        value: my-terraform-state
      - key: S3_VERSIONING
        source: constant
        value: "true"
      - key: S3_ENCRYPTION
        source: constant
        value: "true"


Custom script — subclass AWSScript:
=====================================

    from strata.utils.aws_script_base import AWSScript

    class MyDeployScript(AWSScript):
        def run(self):
            cluster = self.require_env("EKS_CLUSTER")
            region = self.region()          # exits if not set anywhere

            result = self.run_aws([
                "eks", "update-kubeconfig",
                "--name", cluster,
                "--region", region,
            ])
            self.exit_on_failure(result, "aws eks update-kubeconfig")
            self.log(f"EKS credentials fetched for {cluster}")

    if __name__ == "__main__":
        MyDeployScript().execute()


Available helpers on AWSScript:
=================================

    self.run_aws(args)             Run aws subcommand; returns CompletedProcess
    self.exit_on_failure(result)   sys.exit(1) if returncode != 0
    self.require_env("VAR")        Get env var or exit(1) with clear error
    self.env("VAR", default="")    Get env var with optional default
    self.region()                  AWS region (env → profile → exit(1))
    self.account_id()              Account ID via aws sts get-caller-identity
    self.workspace_path()          Path from STRATA_WORKSPACE_PATH
    self.build_path()              Path from STRATA_BUILD_PATH
    self.log("message")            Print to stderr (visible in strata output)

Docs: strata help --topic aws_scripts
"""

# Uncomment and adapt the block below to create your custom script:
#
# import sys
# sys.path.insert(0, '')
# from strata.utils.aws_script_base import AWSScript
#
# class MyScript(AWSScript):
#     def run(self):
#         region = self.region()
#         result = self.run_aws(["sts", "get-caller-identity", "--output", "json"])
#         self.exit_on_failure(result, "aws sts get-caller-identity")
#         self.log("AWS CLI is authenticated")
#
# if __name__ == "__main__":
#     MyScript().execute()
