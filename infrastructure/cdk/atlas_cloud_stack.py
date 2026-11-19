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
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Size,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_events,
    aws_secretsmanager as secretsmanager,
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

# Secrets Manager names the API and jobs Lambdas read at cold start
# (python/lambda_env.py). Created by hand, once per account — the values are
# the box's gspread service_account.json and its Anthropic key.
GSPREAD_SECRET_NAME = "atlas/gspread-service-account"
ANTHROPIC_SECRET_NAME = "atlas/anthropic-api-key"

# Auth (Phase 4, task 6). Per environment, created by hand once (see README):
# the private half of the CloudFront signing key, and the token the email
# Lambda presents to /ingest/email_photo. The public half of the key is
# committed in keys/ and becomes the distribution's key group.
CLOUDFRONT_KEY_SECRET = "atlas/cloudfront-signing-key-{env}"
SERVICE_TOKEN_SECRET = "atlas/service-token-{env}"
KEYS_DIR = Path(__file__).parent / "keys"

# For the few API routes that name no atlas (/create_atlas, /files...).
SCS_ADMIN_GROUP = "scs-admin"

# CloudFront serves this for every 403 from any origin: a private page without
# a valid cookie. Published by scripts/publish_site_assets.py. The API never
# sends 403 for that reason (its auth failures are 401), and both buckets grant
# ListBucket so a missing object is a 404, not a 403 that would read as
# "please log in".
LOGIN_REQUIRED_PAGE = "/local/auth/login-required.html"


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
        callback_host_override: str = None,
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
                # A year, raised from 30 days when Phase 3 made this bucket the
                # source of truth rather than a copy of it.
                #
                # Versioning here is not belt-and-braces: a session's write-back
                # deletes, and it obeys a *successful* run that removed things.
                # A mistaken clear_layer, a bad materialize, a wipe — all reach
                # S3 as ordinary deletions, and the previous object version is
                # the only way back. What it protects is the hand-collected
                # half of the dataswale: hydrants, watertanks, culverts, notes,
                # road mileage, conversations. None of that is re-derivable
                # from an inlet, and Phase 6 removes the box's EBS snapshot as
                # a second copy.
                #
                # Longer than the outlets bucket's 90 days for the same reason
                # that one is longer than 30 was: published outlets can be
                # rebuilt from the layers, and the layers cannot be rebuilt
                # from anything. The cost is noncurrent copies of whatever in
                # staging/ churns — layer and outlet keys are immutable and
                # version-stamped, so they never generate any.
                s3.LifecycleRule(noncurrent_version_expiration=Duration.days(365)),
                # Multipart uploads that failed part-way are invisible in a
                # normal listing and are billed. atlas_store.upload_plan uses
                # upload_file, which goes multipart for the large rasters, so
                # an interrupted seed or publish can leave them behind.
                s3.LifecycleRule(abort_incomplete_multipart_upload_after=Duration.days(7)),
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
            # Publish *mirrors*: stale-key pruning deletes keys the new plan
            # does not write, so without versioning a publish that followed a
            # bad wipe on the box would propagate the wipe and destroy the last
            # good copy. Versioning is what makes this a replica rather than a
            # mirror. 90 days — shorter than the private bucket's 365, because
            # a published outlet can be rebuilt from the layers it was made
            # from, and the layers cannot be rebuilt from anything.
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(noncurrent_version_expiration=Duration.days(90)),
                s3.LifecycleRule(abort_incomplete_multipart_upload_after=Duration.days(7)),
            ],
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

        # --- Light-jobs queue (Phase 4, task 4) ------------------------------
        # Publish, plain layer refreshes and light builds: work that outlives an
        # API request but needs no QGIS. Consumed by the jobs Lambda (below),
        # while the render queue above is consumed by the box worker. Both carry
        # {atlas, id}; the job itself is {atlas}/jobs/{id}.json (python/atlas_jobs.py).
        jobs_dlq = sqs.Queue(
            self, "JobsDlq",
            queue_name=f"atlas-jobs-dlq-{env_name}",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        jobs_queue = sqs.Queue(
            self, "JobsQueue",
            queue_name=f"atlas-jobs-{env_name}",
            # At least the jobs Lambda's 15-minute timeout, so a running job is
            # not handed out again; a busy atlas shortens it to 60 s itself.
            visibility_timeout=Duration.minutes(16),
            retention_period=Duration.days(4),
            enforce_ssl=True,
            # A busy atlas is a receive too; leave room for several retries.
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=10, queue=jobs_dlq),
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
            # The names python/ reads (webapp.JOBS_QUEUE_ENV, atlas_job_consumer).
            "ATLAS_RENDER_QUEUE_URL": render_queue.queue_url,
            "ATLAS_JOBS_QUEUE_URL": jobs_queue.queue_url,
            "ATLAS_JOBS_REGION": self.region,
            "ATLAS_PRIVATE_BUCKET": private_data.bucket_name,
            "ATLAS_OUTLETS_BUCKET": public_outlets.bucket_name,
            # Lambda's only writable disk. Sessions hydrate here; a warm
            # container keeps what it has, so a repeat job downloads only changes.
            "ATLAS_WORKSPACE_ROOT": "/tmp/atlas_workspace",
            "SWALES_ROOT": "/tmp/atlas_workspace",
            "ATLAS_SHARED_DIR": "/tmp/atlas_shared",
            # Read by lambda_env.prepare() at cold start; values never in the template.
            "ATLAS_GSPREAD_SECRET": GSPREAD_SECRET_NAME,
            "ATLAS_ANTHROPIC_SECRET": ANTHROPIC_SECRET_NAME,
        }
        app_secrets = [
            secretsmanager.Secret.from_secret_name_v2(self, "GspreadSecret", GSPREAD_SECRET_NAME),
            secretsmanager.Secret.from_secret_name_v2(self, "AnthropicSecret", ANTHROPIC_SECRET_NAME),
        ]

        # --- Signed cookies for the private tier (task 6) ---------------------
        signing_key = cloudfront.PublicKey(
            self, "SigningKey",
            encoded_key=(KEYS_DIR / f"cloudfront-{env_name}.pem").read_text(),
            comment=f"Atlas private-tier cookies ({env_name})",
        )
        key_group = cloudfront.KeyGroup(self, "SigningKeyGroup", items=[signing_key])
        auth_secrets = [
            secretsmanager.Secret.from_secret_name_v2(
                self, "CloudFrontKeySecret", CLOUDFRONT_KEY_SECRET.format(env=env_name)),
            secretsmanager.Secret.from_secret_name_v2(
                self, "ServiceTokenSecret", SERVICE_TOKEN_SECRET.format(env=env_name)),
        ]

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
                # Synchronous edits (a delta apply, a comment) hydrate one layer;
                # anything longer is a job (Phase 4, task 5).
                memory_size=2048,
                ephemeral_storage_size=Size.gibibytes(4),
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
        jobs_queue.grant_send_messages(api_fn)

        for secret in app_secrets + auth_secrets:
            secret.grant_read(api_fn)
        # API only: the jobs Lambda neither signs cookies nor checks tokens.
        api_fn.add_environment("ATLAS_CLOUDFRONT_KEY_SECRET",
                               CLOUDFRONT_KEY_SECRET.format(env=env_name))
        api_fn.add_environment("ATLAS_SERVICE_TOKEN_SECRET",
                               SERVICE_TOKEN_SECRET.format(env=env_name))
        api_fn.add_environment("CLOUDFRONT_KEY_PAIR_ID", signing_key.public_key_id)

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

        # LIST only from the helper (a missing key is a 404, not a 403 that
        # would read as "please log in"; no viewer request becomes a listing).
        # GetObject is granted by hand below, narrower than the helper's
        # whole-bucket grant.
        private_origin = origins.S3BucketOrigin.with_origin_access_control(
            private_data, origin_access_levels=[cloudfront.AccessLevel.LIST])

        certificate = None
        if certificate_arn and domain_name:
            certificate = acm.Certificate.from_certificate_arn(
                self, "Certificate", certificate_arn,
            )

        distribution = cloudfront.Distribution(
            self, "Distribution",
            comment=f"Stewardship Atlas {env_name}",
            default_behavior=cloudfront.BehaviorOptions(
                # LIST so that a missing key is a 404: with READ alone S3 says
                # 403, which the error response below would turn into "log in".
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    public_outlets,
                    origin_access_levels=[cloudfront.AccessLevel.READ,
                                          cloudfront.AccessLevel.LIST]),
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
                # The private tier (task 6): each atlas's staging tree, from the
                # private bucket, for holders of that atlas's signed cookies
                # (set by /api/auth/callback with a policy for
                # https://{host}/{atlas}/staging/*). The pattern's `*` also
                # matches slashes, so the bucket policy below narrows what
                # CloudFront may read to keys with a staging/ segment.
                "/*/staging/*": cloudfront.BehaviorOptions(
                    origin=private_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    trusted_key_groups=[key_group],
                    # Staging changes on every edit; nothing here is worth a stale copy.
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    compress=True,
                    function_associations=[
                        cloudfront.FunctionAssociation(
                            function=directory_index,
                            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        ),
                    ],
                ),
            },
            # Every 403, from any origin. Not cached, so it can't outlive a login.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=403,
                    response_page_path=LOGIN_REQUIRED_PAGE,
                    ttl=Duration.seconds(0),
                ),
            ],
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

        distribution_arn = (f"arn:aws:cloudfront::{self.account}:distribution/"
                            f"{distribution.distribution_id}")
        private_data.add_to_resource_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[private_data.arn_for_objects("*/staging/*")],
            principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
            conditions={"StringEquals": {"AWS:SourceArn": distribution_arn}},
        ))

        # --- The box writes the read path (Phase 2) --------------------------
        # Publishing an atlas mirrors its public outlets to the outlets bucket
        # and invalidates the atlas prefix (python/atlas_store.py). Until the
        # API moves to Lambda in Phase 4 that publish still runs on the EC2 box,
        # so the box's instance role needs write access here.
        #
        # ListBucket matters as much as the writes: pruning stale keys is what
        # keeps the current/ prefix a true mirror of the published version
        # rather than an accumulating pile of removed layers.
        # The EC2 role is *shared* — one box, imported by every environment's
        # stack. A mutable imported role gets an inline AWS::IAM::Policy whose
        # PolicyName is derived from the construct path, and CDK's hash is
        # relative to the stack root, so every environment produced the same
        # name on the same role: "already managed by another stack".
        #
        # The construct id therefore carries the environment for everything but
        # prod. Prod keeps the original id deliberately, so its existing policy
        # is neither renamed nor replaced — this role is attached to the live
        # box, and a policy swap there is not worth the tidier code.
        role_scope_id = ("WebappEc2Role" if env_name == "prod"
                         else f"WebappEc2Role{env_name.capitalize()}")
        webapp_role = iam.Role.from_role_arn(
            self, role_scope_id,
            f"arn:aws:iam::{self.account}:role/{WEBAPP_EC2_ROLE_NAME}",
            mutable=True,
        )
        public_outlets.grant_read_write(webapp_role)
        public_outlets.grant_delete(webapp_role)

        # Phase 3 (#159): publish also writes *source layer data* — public-tier
        # layers alongside the outlets above, everything else here. The box does
        # that write for the same reason it does the one above: publish still
        # runs on EC2 until Phase 4. Without this the push fails with
        # AccessDenied on PutObject and, because a failed push must never fail a
        # publish, it shows up only in the log.
        #
        # Read and list, not just write: the upload plan reconciles against what
        # the bucket already holds rather than trusting the local catalog's
        # reuse decision, so ListBucket is what stops every publish re-uploading
        # every layer.
        # Read + put, deliberately NOT delete: layer keys are immutable and
        # version-stamped, and the retention decision for #159 is that versions
        # are never deleted. Nothing in the publish path should be able to
        # remove source data from the bucket that holds it.
        private_data.grant_read(webapp_role)
        private_data.grant_put(webapp_role)

        # Step 3 (#159) adds the one thing publish never needed: delete, and
        # only under two prefixes.
        #
        #   {atlas}/staging/*  — staging is a working tree, not an archive. A
        #       consumed delta leaves the pending queue, and an output a run
        #       removed has to leave S3 too, or the next hydrate downloads it
        #       back. Without this every write-back that consumed a delta fails
        #       with AccessDenied *after* its uploads, which is precisely the
        #       half-finished state the resume path exists to repair.
        #   {atlas}/lock       — how a session releases the atlas. A lease that
        #       can only expire would make every session wait out the TTL.
        #
        # The immutable {atlas}/layers/{layer}/{version}/ keys and the catalog
        # stay undeletable, so the retention decision above is untouched: no
        # published version can be removed from the box. Staging deletes are
        # recoverable anyway — the bucket is versioned, so a delete leaves a
        # marker and keeps the previous version.
        private_data.grant_delete(webapp_role, objects_key_pattern="*/staging/*")
        private_data.grant_delete(webapp_role, objects_key_pattern="*/lock")
        # Phase 4, task 4: the box is the render-queue worker
        # (scripts/atlas_worker.py). Receive, delete and extend visibility; job
        # status objects are {atlas}/jobs/*.json, covered by the put above.
        render_queue.grant_consume_messages(webapp_role)
        webapp_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["cloudfront:CreateInvalidation"],
            resources=[
                f"arn:aws:cloudfront::{self.account}:distribution/"
                f"{distribution.distribution_id}"
            ],
        ))

        if api_image_tag:
            # The jobs Lambda: the API image with the queue consumer as its
            # handler. Separate from the API function for its limits — a
            # publish or a layer refresh may take minutes, and hydrating an
            # atlas into /tmp needs room — not for its code.
            jobs_fn = lambda_.DockerImageFunction(
                self, "JobsFunction",
                code=lambda_.DockerImageCode.from_ecr(
                    api_repo, tag_or_digest=api_image_tag,
                    cmd=["jobs_handler.handler"]),
                memory_size=3008,
                ephemeral_storage_size=Size.gibibytes(10),
                timeout=Duration.minutes(15),
                environment=dict(
                    api_env,
                    # Publish mirrors to the outlets bucket and invalidates the
                    # atlas prefix. Only the jobs Lambda publishes, and unlike the
                    # API function it is not an origin of this distribution, so
                    # naming the distribution here makes no dependency cycle.
                    ATLAS_DISTRIBUTION_ID=distribution.distribution_id,
                    ATLAS_PUBLIC_BASE_URL=(f"https://{domain_name}" if domain_name else
                                           f"https://{distribution.distribution_domain_name}"),
                ),
            )
            jobs_fn.add_event_source(lambda_events.SqsEventSource(
                jobs_queue, batch_size=1, report_batch_item_failures=True))
            private_data.grant_read_write(jobs_fn)
            private_data.grant_delete(jobs_fn, objects_key_pattern="*/staging/*")
            private_data.grant_delete(jobs_fn, objects_key_pattern="*/lock")
            public_outlets.grant_read_write(jobs_fn)
            public_outlets.grant_delete(jobs_fn)
            for secret in app_secrets:
                secret.grant_read(jobs_fn)
            jobs_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["cloudfront:CreateInvalidation"],
                resources=[f"arn:aws:cloudfront::{self.account}:distribution/"
                           f"{distribution.distribution_id}"],
            ))

        # --- Cognito ---------------------------------------------------------
        # Replaced 2026-11-19 (task 6): sign-in by username with email optional,
        # because the accounts are shared logins (`scvfd`, westport's `admin`
        # and `internal`) with no mailbox behind them, and a relaxed password
        # policy so their current passwords carry over. Sign-in aliases can't
        # change in place, so this is a new pool under a new construct id. The
        # old pool (0 users) is RETAIN and stays in the account, holding the
        # old domain prefix -- hence the new one.
        user_pool = cognito.UserPool(
            self, "Users",
            user_pool_name=f"atlas-users-{env_name}",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True),
            sign_in_case_sensitive=False,
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=False, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=False,
                require_uppercase=False,
                require_digits=False,
                require_symbols=False,
            ),
            # No email to recover through; an admin resets passwords.
            account_recovery=cognito.AccountRecovery.NONE,
            removal_policy=RemovalPolicy.RETAIN,
        )
        cognito_domain_prefix = f"scs-atlas-auth-{env_name}"
        user_pool.add_domain(
            "UsersDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=cognito_domain_prefix),
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

        # Deriving this from the distribution creates a circular dependency,
        # and it is only invisible in prod because `domain_name` is set there:
        #
        #   api_fn --env USER_POOL_CLIENT_ID--> WebClient
        #          --callback_urls--> Distribution --origin--> api_fn's URL
        #
        # With a literal domain the middle edge does not exist. Without one
        # CloudFormation refuses the whole stack, so the substrate could not be
        # created from scratch in any environment that has no custom domain --
        # which is exactly what `env_name=staging` is for.
        #
        # The callback host is therefore never taken from the distribution. It
        # comes from the domain when there is one, from `-c callback_host=` when
        # there is not, and otherwise from a placeholder. Cognito callbacks only
        # matter once SSO is actually used (Phase 4), and the value can be
        # corrected by a second deploy once the distribution's domain is known.
        callback_host = (domain_name or callback_host_override
                         or 'localhost.invalid')
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
                # CloudFront routes /api/* to the API, which serves /auth/*.
                callback_urls=[f"https://{callback_host}/api/auth/callback"],
                logout_urls=[f"https://{callback_host}/"],
            ),
            # The ID token is the session (python/webapp_auth.py), and a day is
            # Cognito's limit for it; the CloudFront cookies expire with it.
            id_token_validity=Duration.days(1),
            access_token_validity=Duration.days(1),
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
        cognito.CfnUserPoolGroup(
            self, "GroupScsAdmin",
            user_pool_id=user_pool.user_pool_id,
            group_name=SCS_ADMIN_GROUP,
            description="SCS staff: the API routes that name no atlas",
        )

        api_fn.add_environment("USER_POOL_ID", user_pool.user_pool_id)
        api_fn.add_environment("USER_POOL_CLIENT_ID", web_client.user_pool_client_id)
        api_fn.add_environment(
            "COGNITO_DOMAIN", f"{cognito_domain_prefix}.auth.{self.region}.amazoncognito.com")
        api_fn.add_environment("ATLAS_PUBLIC_HOST", callback_host)

        # --- Outputs ---------------------------------------------------------
        CfnOutput(self, "DistributionDomain", value=distribution.distribution_domain_name)
        CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        CfnOutput(self, "PrivateDataBucket", value=private_data.bucket_name)
        CfnOutput(self, "PublicOutletsBucket", value=public_outlets.bucket_name)
        CfnOutput(self, "RenderQueueUrl", value=render_queue.queue_url)
        CfnOutput(self, "JobsQueueUrl", value=jobs_queue.queue_url)
        CfnOutput(self, "ApiRepositoryUri", value=api_repo.repository_uri)
        CfnOutput(self, "ApiFunctionName", value=api_fn.function_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=web_client.user_pool_client_id)
        CfnOutput(self, "SigningKeyId", value=signing_key.public_key_id)
