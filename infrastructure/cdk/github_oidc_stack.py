"""GitHub Actions deploy identity: OIDC provider + assumable role.

No long-lived AWS keys in GitHub. Actions exchanges its OIDC token for this
role, which is only permitted to assume the CDK bootstrap roles and push to the
atlas-api ECR repositories.

Deployed once, from a laptop:
    cdk deploy AtlasGithubOidc --profile atlas

Additive only — this creates a new provider and a new role, and touches nothing
that already exists.
"""

from aws_cdk import CfnOutput, Stack, aws_iam as iam
from constructs import Construct

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"


class GithubOidcStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        repo: str,
        role_name: str = "atlas-github-deploy",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        provider = iam.OpenIdConnectProvider(
            self, "GithubOidcProvider",
            url=GITHUB_OIDC_URL,
            client_ids=["sts.amazonaws.com"],
        )

        # Any branch of this repo, but only this repo: a fork's token carries
        # "repo:<fork>/..." and a pull_request token carries ":pull_request",
        # so neither matches. Tighten to refs/heads/main once the workflow is
        # settled and deploys only happen from main.
        deploy_role = iam.Role(
            self, "DeployRole",
            role_name=role_name,
            description="Assumed by GitHub Actions to run cdk deploy",
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub":
                            f"repo:{repo}:ref:refs/heads/*",
                    },
                },
            ),
        )

        # CDK's modern bootstrap does the real work through its own roles; the
        # deploy identity only needs to assume them.
        deploy_role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*"],
        ))
        deploy_role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:*:{self.account}:parameter/cdk-bootstrap/hnb659fds/version",
            ],
        ))

        # Pushing the API container image happens in the workflow, before the
        # deploy, so it needs ECR directly.
        deploy_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken"],
            resources=["*"],
        ))
        deploy_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:CompleteLayerUpload",
                "ecr:DescribeImages",
                "ecr:DescribeRepositories",
                "ecr:InitiateLayerUpload",
                "ecr:PutImage",
                "ecr:UploadLayerPart",
            ],
            resources=[
                f"arn:aws:ecr:*:{self.account}:repository/atlas-api-*",
            ],
        ))

        CfnOutput(self, "DeployRoleArn", value=deploy_role.role_arn)
