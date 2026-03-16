"""
CDK stack for the email photo inlet pipeline.

Resources:
  - S3 ingress bucket (SES delivers raw emails here)
  - Lambda function (triggered by S3, POSTs to webapp)
  - IAM role for Lambda
  - SES receipt rule set + receipt rule
"""
import os
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    BundlingOptions,
    aws_s3 as s3,
    aws_lambda as lambda_,
    aws_s3_notifications as s3n,
    aws_iam as iam,
    aws_ses as ses,
    aws_ses_actions as ses_actions,
)
from constructs import Construct


class EmailPhotoStack(Stack):

    def __init__(self, scope: Construct, construct_id: str,
                 webapp_url: str,
                 receipt_addresses: list[str],
                 **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # --- IAM role for EC2 webapp server ---
        # Attach to the SCVFD EC2 instance so the webapp can write images to S3
        # without credentials on disk.
        ec2_role = iam.Role(
            self, "AtlasWebappEC2Role",
            role_name="atlas-webapp-ec2-role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )

        ec2_instance_profile = iam.CfnInstanceProfile(
            self, "AtlasWebappInstanceProfile",
            instance_profile_name="atlas-webapp-instance-profile",
            roles=[ec2_role.role_name],
        )

        # --- S3 data bucket (stores processed images permanently) ---
        data_bucket = s3.Bucket(
            self, "AtlasDataBucket",
            bucket_name="scs-atlas-data",
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Grant EC2 webapp role write access to image storage
        data_bucket.grant_put(ec2_role)

        # Grant EC2 webapp role read access to CloudWatch Logs (for /log endpoint)
        ec2_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:DescribeLogStreams", "logs:GetLogEvents"],
            resources=[f"arn:aws:logs:*:*:log-group:/aws/lambda/atlas-email-photo-handler:*"],
        ))

        # Grant EC2 webapp role permission to send bounce emails via SES
        ec2_role.add_to_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"],
        ))

        # --- S3 ingress bucket ---
        ingress_bucket = s3.Bucket(
            self, "AtlasIngressBucket",
            bucket_name="atlas-ingress",
            removal_policy=RemovalPolicy.RETAIN,  # don't delete emails on stack destroy
            lifecycle_rules=[
                s3.LifecycleRule(
                    # Auto-delete raw emails after 30 days (processed ones deleted by Lambda)
                    expiration=Duration.days(30),
                )
            ],
        )

        # --- Lambda IAM role ---
        lambda_role = iam.Role(
            self, "EmailPhotoLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Read + delete from ingress bucket
        ingress_bucket.grant_read(lambda_role)
        ingress_bucket.grant_delete(lambda_role)

        # --- Lambda function ---
        email_lambda = lambda_.Function(
            self, "EmailPhotoHandler",
            function_name="atlas-email-photo-handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="email_photo_handler.handler",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "lambda_build")
            ),
            role=lambda_role,
            timeout=Duration.seconds(30),
            environment={
                "WEBAPP_URL": webapp_url,
            },
        )

        # Allow SES to invoke the Lambda (needed if using Lambda action directly;
        # with S3 trigger this is handled by S3 notification permissions)
        email_lambda.add_permission(
            "AllowS3Invoke",
            principal=iam.ServicePrincipal("s3.amazonaws.com"),
            source_arn=ingress_bucket.bucket_arn,
        )

        # S3 → Lambda trigger
        ingress_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(email_lambda),
        )

        # --- SES: allow SES to write to the ingress bucket ---
        ingress_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[ingress_bucket.arn_for_objects("*")],
                conditions={
                    "StringEquals": {
                        "aws:Referer": self.account,
                    }
                },
            )
        )

        # --- SES receipt rule set ---
        rule_set = ses.ReceiptRuleSet(
            self, "AtlasReceiptRuleSet",
            receipt_rule_set_name="atlas-email-inlet",
        )

        rule_set.add_rule(
            "DeliverToS3",
            recipients=receipt_addresses,
            actions=[
                ses_actions.S3(
                    bucket=ingress_bucket,
                    object_key_prefix="incoming/",
                )
            ],
            scan_enabled=True,
            tls_policy=ses.TlsPolicy.REQUIRE,
        )
