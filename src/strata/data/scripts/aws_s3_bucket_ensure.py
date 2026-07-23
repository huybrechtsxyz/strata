"""Built-in strata script: ensure an S3 bucket exists.

Runs ``aws s3api create-bucket`` — idempotent: if the bucket already exists and
is owned by the same account the command succeeds silently.

Common uses:
- Ensure Terraform remote state bucket exists before ``strata deploy run``
- Ensure artifact storage bucket exists before a build stage

Required environment variables:
    S3_BUCKET           — S3 bucket name

Optional:
    AWS_DEFAULT_REGION  — AWS region (auto-resolved if not set)
    S3_VERSIONING       — Set to "true" to enable versioning (recommended for TF state)
    S3_ENCRYPTION       — Set to "true" to enable AES-256 server-side encryption
    S3_BLOCK_PUBLIC     — Set to "true" to apply public access block (default: true)
    S3_TAGS             — Comma-separated key=value tags (e.g. "env=prod,team=platform")

Usage in workspace YAML:
    lifecycle:
      pre_provision:
        scripts:
          - <strata_data>/scripts/aws_s3_bucket_ensure.py

    variables:
      - key: S3_BUCKET
        source: constant
        value: my-terraform-state-bucket
      - key: S3_VERSIONING
        source: constant
        value: "true"
      - key: S3_ENCRYPTION
        source: constant
        value: "true"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from strata.utils.aws_script_base import AWSScript


class S3BucketEnsure(AWSScript):
    """Create an S3 bucket if it does not already exist."""

    def run(self) -> None:
        bucket = self.require_env("S3_BUCKET")
        aws_region = self.region()
        versioning = self.env("S3_VERSIONING", "false").lower() == "true"
        encryption = self.env("S3_ENCRYPTION", "false").lower() == "true"
        block_public = self.env("S3_BLOCK_PUBLIC", "true").lower() != "false"
        tags_raw = self.env("S3_TAGS")

        # Create bucket (idempotent — succeeds even if already exists in same account)
        args = ["s3api", "create-bucket", "--bucket", bucket, "--region", aws_region]
        # us-east-1 must NOT have --create-bucket-configuration
        if aws_region != "us-east-1":
            args += ["--create-bucket-configuration", f"LocationConstraint={aws_region}"]

        self.log(f"Ensuring S3 bucket '{bucket}' exists in '{aws_region}'")
        result = self.run_aws(args)
        # 409 BucketAlreadyOwnedByYou is idempotent — not a failure
        if result.returncode != 0 and "BucketAlreadyOwnedByYou" not in result.stderr:
            self.exit_on_failure(result, "aws s3api create-bucket")
        else:
            self.log(f"Bucket '{bucket}' ready")

        # Apply public access block (security best practice)
        if block_public:
            block_cfg = json.dumps(
                {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                }
            )
            r = self.run_aws(
                [
                    "s3api",
                    "put-public-access-block",
                    "--bucket",
                    bucket,
                    "--public-access-block-configuration",
                    block_cfg,
                ]
            )
            if r.returncode == 0:
                self.log("Public access block applied")

        # Versioning
        if versioning:
            r = self.run_aws(
                [
                    "s3api",
                    "put-bucket-versioning",
                    "--bucket",
                    bucket,
                    "--versioning-configuration",
                    "Status=Enabled",
                ]
            )
            if r.returncode == 0:
                self.log("Versioning enabled")

        # Server-side encryption
        if encryption:
            enc_cfg = json.dumps({"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
            r = self.run_aws(
                [
                    "s3api",
                    "put-bucket-encryption",
                    "--bucket",
                    bucket,
                    "--server-side-encryption-configuration",
                    enc_cfg,
                ]
            )
            if r.returncode == 0:
                self.log("AES-256 encryption enabled")

        # Tags
        if tags_raw:
            tag_list = []
            for pair in tags_raw.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    tag_list.append({"Key": k.strip(), "Value": v.strip()})
            if tag_list:
                tag_set = json.dumps({"TagSet": tag_list})
                r = self.run_aws(
                    [
                        "s3api",
                        "put-bucket-tagging",
                        "--bucket",
                        bucket,
                        "--tagging",
                        tag_set,
                    ]
                )
                if r.returncode == 0:
                    self.log(f"Tags applied ({len(tag_list)} tag(s))")


if __name__ == "__main__":
    S3BucketEnsure().execute()
