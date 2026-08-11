#!/usr/bin/env python3
import aws_cdk as cdk
from email_photo_stack import EmailPhotoStack
from atlas_provisioning_stack import AtlasDnsStack, AtlasSecurityGroupStack
from atlas_cloud_stack import AtlasCloudStack
from atlas_cert_stack import AtlasCertStack
from github_oidc_stack import GithubOidcStack

app = cdk.App()

# --- Email photo inlet (SES → S3 → Lambda → webapp) ---
EmailPhotoStack(
    app, "AtlasEmailPhotoStack",
    webapp_url="https://fireatlas.org:9000",
    receipt_addresses=[
        "scvfd@fireatlas.org",
    ],
    env=cdk.Environment(
        account="438886543302",
        region="us-east-1",
    ),
)

# --- Per-atlas DNS + security group provisioning ---
# EC2 instance: i-088b520a5a06d51ac (us-west-1, atlas account 438886543302)
# Route 53 hosted zone for fireatlas.org: personal account

EC2_IP = "54.241.152.98"
EC2_SG = "sg-00acc74a2e9943cf8"
FIREATLAS_ZONE_ID = "Z053813635B6S6EHA5GKU"
PERSONAL_ACCOUNT = "280439772481"   # personal account owns fireatlas.org Route 53
ATLAS_ACCOUNT = "438886543302"

AtlasDnsStack(
    app, "KennedyDns",
    atlas_name="kennedy",
    ec2_ip=EC2_IP,
    hosted_zone_id=FIREATLAS_ZONE_ID,
    env=cdk.Environment(account=PERSONAL_ACCOUNT, region="us-east-1"),
)

AtlasSecurityGroupStack(
    app, "KennedySecurityGroup",
    atlas_name="kennedy",
    security_group_id=EC2_SG,
    ports=[8887, 9997],
    env=cdk.Environment(account=ATLAS_ACCOUNT, region="us-west-1"),
)

# --- Cloud-native substrate (documents/cloud_native_plan.md, Phase 1) ---------
# us-east-1 throughout: CloudFront requires its ACM cert there, and the existing
# scs-atlas-data bucket and email stack already live there.

CLOUD_ENV = cdk.Environment(account=ATLAS_ACCOUNT, region="us-east-1")

# Atlases that get Cognito groups ({atlas}-admin, {atlas}-internal). Adding an
# atlas here needs a deploy; adding a *user* to a group does not.
ATLASES = [
    "scvfd",
    "wvfd",
    "wvfd_dev",
    "kennedy",
    "MineralKinsey",
    "wildwood",
    "king_range",
    "samuelsloop",
]

env_name = app.node.try_get_context("env_name") or "prod"
domain_name = app.node.try_get_context("atlas_domain")
certificate_arn = app.node.try_get_context("certificate_arn")
google_client_id = app.node.try_get_context("google_client_id")
google_secret_name = app.node.try_get_context("google_secret_name")
# Set by CI (-c api_image_tag=<sha>) after it pushes the image. Without it the
# API Lambda deploys as an inline placeholder so a laptop with no Docker can
# still bring the stack up.
api_image_tag = app.node.try_get_context("api_image_tag")

AtlasCertStack(
    app, f"AtlasCert-{env_name}",
    domain_name=domain_name,
    env=CLOUD_ENV,
)

AtlasCloudStack(
    app, f"AtlasCloud-{env_name}",
    env_name=env_name,
    atlases=ATLASES,
    domain_name=domain_name,
    certificate_arn=certificate_arn,
    google_client_id=google_client_id,
    google_secret_name=google_secret_name,
    api_image_tag=api_image_tag,
    env=CLOUD_ENV,
)

GithubOidcStack(
    app, "AtlasGithubOidc",
    repo="Salmon-Creek-Systems/stewardship_atlas",
    env=CLOUD_ENV,
)

app.synth()
