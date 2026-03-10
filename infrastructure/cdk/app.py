#!/usr/bin/env python3
import aws_cdk as cdk
from email_photo_stack import EmailPhotoStack

app = cdk.App()

EmailPhotoStack(
    app, "AtlasEmailPhotoStack",
    webapp_url="https://scvfd.fireatlas.org:9999",
    receipt_addresses=[
        "scvfd@fireatlas.org",
    ],
    env=cdk.Environment(
        account="438886543302",
        region="us-east-1",
    ),
)

app.synth()
