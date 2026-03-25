#!/usr/bin/env python3
import aws_cdk as cdk
from email_photo_stack import EmailPhotoStack
from atlas_provisioning_stack import AtlasDnsStack, AtlasSecurityGroupStack

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

app.synth()
