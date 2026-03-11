"""
CDK stacks for per-atlas provisioning.

Two stacks per atlas because DNS (Route 53) lives in the personal AWS account
while the EC2 security group lives in the atlas account (us-west-1).

Usage in app.py:
    AtlasDnsStack(app, "KennedyDns",
        atlas_name="kennedy",
        ec2_ip="54.241.152.98",
        hosted_zone_id="Z053813635B6S6EHA5GKU",
        env=cdk.Environment(account="<personal-account>", region="us-east-1"),
    )
    AtlasSecurityGroupStack(app, "KennedySecurityGroup",
        atlas_name="kennedy",
        security_group_id="sg-00acc74a2e9943cf8",
        ports=[8887, 9997],
        env=cdk.Environment(account="438886543302", region="us-west-1"),
    )
"""
from aws_cdk import (
    Stack,
    aws_route53 as route53,
    aws_ec2 as ec2,
)
from constructs import Construct


class AtlasDnsStack(Stack):
    """
    Creates an A record for {atlas_name}.fireatlas.org pointing at the EC2 instance.
    Must be deployed to the personal AWS account that owns the fireatlas.org hosted zone.
    """

    def __init__(self, scope: Construct, construct_id: str,
                 atlas_name: str,
                 ec2_ip: str,
                 hosted_zone_id: str,
                 **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self, "FireatlasZone",
            hosted_zone_id=hosted_zone_id,
            zone_name="fireatlas.org",
        )

        route53.ARecord(
            self, f"{atlas_name}ARecord",
            zone=hosted_zone,
            record_name=atlas_name,
            target=route53.RecordTarget.from_ip_addresses(ec2_ip),
        )


class AtlasSecurityGroupStack(Stack):
    """
    Opens the webapp and Jupyter ports on the EC2 security group for a new atlas.
    Must be deployed to the atlas AWS account (438886543302) in us-west-1.
    """

    def __init__(self, scope: Construct, construct_id: str,
                 atlas_name: str,
                 security_group_id: str,
                 ports: list[int],
                 **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        sg = ec2.SecurityGroup.from_security_group_id(
            self, "AtlasEC2SG",
            security_group_id=security_group_id,
            mutable=True,
        )

        for port in ports:
            sg.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(port),
                description=f"{atlas_name} port {port}",
            )
