"""Cloud-native substrate for Stewardship Atlas (Phase 1 of documents/cloud_native_plan.md).

One stack holding the pieces the migration builds on:

  - two buckets: private source data, public outlets
  - CloudFront distribution (the single front door) with Origin Access Control
  - Cognito user pool with Google IdP and per-atlas/per-role groups
  - SQS queue for QGIS render jobs (Phase 5)
  - an API Lambda placeholder + its ECR repository (Phase 4)

Phase 2 added the pieces that make the read path actually servable: a
viewer-request function so directory URLs resolve to index.html, write access to
the outlets bucket for the box that publishes into it, and a cache TTL raised now
that publish invalidates.

Nothing customer-facing points at any of this yet. The stack is deployable on its
own and every resource that holds data is RETAIN on delete.

Deploy:
    cd infrastructure/cdk && cdk deploy AtlasCloud-prod --profile atlas

See README.md in this directory for the toolchain and the one-time manual steps.
"""

import re

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_sqs as sqs,
)
from constructs import Construct

# Roles that exist for every atlas. These become Cognito groups named
# "{atlas}-{role}"; the gated path intersects the JWT "groups" claim with the
# outlet's required access level. Membership is data, not code — adding a user
# to a group is a console/CLI edit, no deploy.
ATLAS_ROLES = ("admin", "internal")

# Stand-in until the real FastAPI container lands in Phase 4. Only used when no
# api_image_tag is supplied (i.e. a laptop deploy with no Docker available).
PLACEHOLDER_SOURCE = """
import json


def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"status": "placeholder", "phase": 1}),
    }
"""


# Outlets are directories: /kennedy/current/outlets/webmap/ needs to resolve to
# that directory's index.html. CloudFront's default_root_object only covers the
# distribution root, and an S3 origin behind OAC does no directory indexing, so
# without this every outlet URL 403s (an empty/missing key under OAC returns 403,
# not 404 — see the Phase 1 gotchas). Viewer-request function on the static
# behavior only; /api/* has its own behavior and is untouched.
DIRECTORY_INDEX_SOURCE = """
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
    } else if (!uri.split('/').pop().includes('.')) {
        request.uri = uri + '/index.html';
    }
    return request;
}
"""

# The box publishes into the read path. Imported by ARN rather than wired as a
# cross-stack reference: the role belongs to AtlasEmailPhotoStack, and an export
# would couple the two stacks' deploy order for what is just a policy attachment.
WEBAPP_EC2_ROLE_NAME = "atlas-webapp-ec2-role"


def _logical(name: str) -> str:
    """CloudFormation logical IDs are alphanumeric; atlas names are not."""
    return re.sub(r"[^A-Za-z0-9]", "", name.title())


class AtlasCloudStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        atlases: list,
        domain_name: str = None,
        certificate_arn: str = None,
        google_client_id: str = None,
        google_secret_name: str = None,
        api_image_tag: str = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Buckets ---------------------------------------------------------
        # Private: the dataswale itself (layers, deltas, versions) plus outlets
        # whose access level is not "public". Reachable only via IAM — the
        # protected read path goes through the API Lambda, never direct.
        private_data = s3.Bucket(
            self, "PrivateData",
            bucket_name=f"scs-atlas-private-{env_name}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(noncurrent_version_expiration=Duration.days(30)),
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Public outlets: served static through CloudFront. Public access is
        # still blocked at the bucket — CloudFront reaches it via OAC, so there
        # is no way to address the bucket directly.
        public_outlets = s3.Bucket(
            self, "PublicOutlets",
            bucket_name=f"scs-atlas-outlets-{env_name}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            # COG and PMTiles are read with HTTP range requests from the browser.
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.HEAD],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    exposed_headers=[
                        "ETag", "Content-Range", "Accept-Ranges", "Content-Length",
                    ],
                    max_age=3600,
                ),
            ],
        )

        # --- Render queue (Phase 5) ------------------------------------------
        render_dlq = sqs.Queue(
            self, "RenderDlq",
            queue_name=f"atlas-render-dlq-{env_name}",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        render_queue = sqs.Queue(
            self, "RenderQueue",
            queue_name=f"atlas-render-{env_name}",
            # A QGIS runbook render is minutes, not seconds.
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(4),
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=render_dlq),
        )

        # --- API Lambda + its image repository (Phase 4) ---------------------
        api_repo = ecr.Repository(
            self, "ApiRepo",
            repository_name=f"atlas-api-{env_name}",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
        )

        api_env = {
            "ATLAS_ENV": env_name,
            "PRIVATE_BUCKET": private_data.bucket_name,
            "PUBLIC_BUCKET": public_outlets.bucket_name,
            "RENDER_QUEUE_URL": render_queue.queue_url,
        }

        # Deliberately unnamed. Switching between a zip and a container image
        # replaces the function, and CloudFormation refuses to replace a
        # custom-named resource — the new one would collide with the old one's
        # name mid-update. The generated name is the price of being able to
        # change packaging; use the ApiFunctionName output to find it.
        if api_image_tag:
            # Normal path: CI built and pushed the image, and passes its tag.
            api_fn = lambda_.DockerImageFunction(
                self, "ApiFunction",
                code=lambda_.DockerImageCode.from_ecr(api_repo, tag_or_digest=api_image_tag),
                memory_size=1024,
                timeout=Duration.seconds(30),
                environment=api_env,
            )
        else:
            # Bootstrap path: no Docker (e.g. a laptop deploy). Swapping between
            # the two replaces the function, which is fine — but don't flip back
            # and forth casually, the function URL changes with it.
            api_fn = lambda_.Function(
                self, "ApiFunction",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="index.handler",
                code=lambda_.Code.from_inline(PLACEHOLDER_SOURCE),
                memory_size=512,
                timeout=Duration.seconds(30),
                environment=api_env,
            )

        private_data.grant_read_write(api_fn)
        public_outlets.grant_read_write(api_fn)
        render_queue.grant_send_messages(api_fn)

        # IAM auth on the function URL: the only caller allowed to sign requests
        # is this distribution, via OAC. There is no unauthenticated public URL.
        api_url = api_fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)

        # --- CloudFront: the single front door -------------------------------
        # Phase 2 wired invalidation into publish (atlas_store.invalidate_current),
        # so the 5-minute stopgap TTL is no longer needed. Published content only
        # changes on publish, and publish now invalidates the atlas prefix.
        outlet_cache_policy = cloudfront.CachePolicy(
            self, "OutletCachePolicy",
            cache_policy_name=f"atlas-outlets-{env_name}",
            default_ttl=Duration.hours(1),
            min_ttl=Duration.seconds(0),
            max_ttl=Duration.days(1),
            enable_accept_encoding_gzip=True,
            enable_accept_encoding_brotli=True,
        )

        directory_index = cloudfront.Function(
            self, "DirectoryIndex",
            function_name=f"atlas-directory-index-{env_name}",
            code=cloudfront.FunctionCode.from_inline(DIRECTORY_INDEX_SOURCE),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="Append index.html to directory URIs",
        )

        certificate = None
        if certificate_arn and domain_name:
            certificate = acm.Certificate.from_certificate_arn(
                self, "Certificate", certificate_arn,
            )

        distribution = cloudfront.Distribution(
            self, "Distribution",
            comment=f"Stewardship Atlas {env_name}",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(public_outlets),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=outlet_cache_policy,
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=directory_index,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    ),
                ],
            ),
            additional_behaviors={
                # Everything mutating, and every protected read, goes here.
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.FunctionUrlOrigin.with_origin_access_control(api_url),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
            default_root_object="index.html",
            domain_names=[domain_name] if certificate else None,
            certificate=certificate,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )

        # Lambda-URL OAC needs *two* grants, and CDK's
        # with_origin_access_control() only adds the first. Without this second
        # one CloudFront's signed request is rejected before the function is
        # ever invoked — a 403 with no log group to show for it.
        # See "Restrict access to an AWS Lambda function URL origin" in the
        # CloudFront developer guide.
        api_fn.add_permission(
            "AllowCloudFrontInvoke",
            principal=iam.ServicePrincipal("cloudfront.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=(
                f"arn:aws:cloudfront::{self.account}:distribution/"
                f"{distribution.distribution_id}"
            ),
        )

        # --- The box writes the read path (Phase 2) --------------------------
        # Publishing an atlas mirrors its public outlets to the outlets bucket
        # and invalidates the atlas prefix (python/atlas_store.py). Until the
        # API moves to Lambda in Phase 4 that publish still runs on the EC2 box,
        # so the box's instance role needs write access here.
        #
        # ListBucket matters as much as the writes: pruning stale keys is what
        # keeps the current/ prefix a true mirror of the published version
        # rather than an accumulating pile of removed layers.
        webapp_role = iam.Role.from_role_arn(
            self, "WebappEc2Role",
            f"arn:aws:iam::{self.account}:role/{WEBAPP_EC2_ROLE_NAME}",
            mutable=True,
        )
        public_outlets.grant_read_write(webapp_role)
        public_outlets.grant_delete(webapp_role)
        webapp_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["cloudfront:CreateInvalidation"],
            resources=[
                f"arn:aws:cloudfront::{self.account}:distribution/"
                f"{distribution.distribution_id}"
            ],
        ))

        # --- Cognito ---------------------------------------------------------
        user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name=f"atlas-{env_name}",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        user_pool.add_domain(
            "UserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"scs-atlas-{env_name}",
            ),
        )

        identity_providers = [cognito.UserPoolClientIdentityProvider.COGNITO]
        google_idp = None
        if google_client_id and google_secret_name:
            google_idp = cognito.UserPoolIdentityProviderGoogle(
                self, "GoogleIdp",
                user_pool=user_pool,
                client_id=google_client_id,
                client_secret_value=SecretValue.secrets_manager(google_secret_name),
                scopes=["openid", "email", "profile"],
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.GOOGLE_EMAIL,
                ),
            )
            identity_providers.append(cognito.UserPoolClientIdentityProvider.GOOGLE)

        callback_host = domain_name or distribution.distribution_domain_name
        web_client = user_pool.add_client(
            "WebClient",
            user_pool_client_name=f"atlas-web-{env_name}",
            generate_secret=False,
            prevent_user_existence_errors=True,
            supported_identity_providers=identity_providers,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[f"https://{callback_host}/auth/callback"],
                logout_urls=[f"https://{callback_host}/"],
            ),
        )
        if google_idp is not None:
            # The client can't reference the IdP before it exists.
            web_client.node.add_dependency(google_idp)

        # A federated user lands with no groups and therefore no access.
        # Onboarding is a group-membership edit, not a deploy.
        for atlas_name in atlases:
            for role in ATLAS_ROLES:
                cognito.CfnUserPoolGroup(
                    self, f"Group{_logical(atlas_name)}{role.title()}",
                    user_pool_id=user_pool.user_pool_id,
                    group_name=f"{atlas_name}-{role}",
                    description=f"{role} access to the {atlas_name} atlas",
                )

        api_fn.add_environment("USER_POOL_ID", user_pool.user_pool_id)
        api_fn.add_environment("USER_POOL_CLIENT_ID", web_client.user_pool_client_id)

        # --- Outputs ---------------------------------------------------------
        CfnOutput(self, "DistributionDomain", value=distribution.distribution_domain_name)
        CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        CfnOutput(self, "PrivateDataBucket", value=private_data.bucket_name)
        CfnOutput(self, "PublicOutletsBucket", value=public_outlets.bucket_name)
        CfnOutput(self, "RenderQueueUrl", value=render_queue.queue_url)
        CfnOutput(self, "ApiRepositoryUri", value=api_repo.repository_uri)
        CfnOutput(self, "ApiFunctionName", value=api_fn.function_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=web_client.user_pool_client_id)
