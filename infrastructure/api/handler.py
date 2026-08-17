"""Placeholder API handler (Phase 1).

Exists so the deploy spine — CI builds an image, pushes to ECR, cdk deploy points
the Lambda at the tag, CloudFront routes /api/* to it — can be verified before any
real code moves. Reports what the substrate wired up so a single curl proves the
whole chain.

Replaced in Phase 4 by the FastAPI app behind the Lambda Web Adapter.
"""

import json
import os

FIELDS = (
    "ATLAS_ENV",
    "PRIVATE_BUCKET",
    "PUBLIC_BUCKET",
    "RENDER_QUEUE_URL",
    "USER_POOL_ID",
    "USER_POOL_CLIENT_ID",
)


def handler(event, context):
    body = {
        "status": "placeholder",
        "phase": 1,
        "image": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION"),
        "wired": {name: bool(os.environ.get(name)) for name in FIELDS},
    }
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
