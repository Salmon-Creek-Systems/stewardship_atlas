"""Automated backups for the EC2 box (us-west-1).

Until the box is retired at Phase 6, the dataswale lives on one EBS volume:
layers, deltas, published versions, photos. Config is in git and inlet-derived
layers (OSM, Overture, LANDFIRE, terrain) are re-derivable, but the
hand-collected data — hydrants, watertanks, culverts, notes, road mileage,
conversations — is not. It existed in exactly one place, unsnapshotted, until
2026-08-19.

The threat is not only an attacker on the still-open :9000 API. A mistaken
clear_layer during testing, or the volume simply failing, loses the same data.

Deliberately its own stack because it must live in us-west-1 with the volume,
while the cloud substrate is us-east-1 (CloudFront's cert has to be there).

Deploy:
    cd infrastructure/cdk && cdk deploy AtlasBackup --profile atlas
"""

from aws_cdk import (
    CfnOutput,
    CfnTag,
    Stack,
    aws_dlm as dlm,
    aws_iam as iam,
)
from constructs import Construct

# DLM targets volumes by tag, so the volume carries `Backup=atlas-daily`. A tag
# rather than a hardcoded volume id means a replaced or added volume is covered
# by tagging it, with no deploy.
BACKUP_TAG_KEY = "Backup"
BACKUP_TAG_VALUE = "atlas-daily"


class AtlasBackupStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # DLM needs a service role. AWS offers a default one created out of
        # band; an explicit role keeps the permission grant visible in code.
        dlm_role = iam.Role(
            self, "DlmRole",
            role_name="atlas-dlm-snapshot-role",
            assumed_by=iam.ServicePrincipal("dlm.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSDataLifecycleManagerServiceRole"
                ),
            ],
        )

        # Two schedules: dailies to undo a recent mistake, weeklies so damage
        # that goes unnoticed for a while is still recoverable. Snapshots are
        # incremental, so the second schedule costs little beyond the first.
        # Times are UTC — 09:00Z is ~02:00 Pacific, the quiet part of the day.
        daily = dlm.CfnLifecyclePolicy.ScheduleProperty(
            name="daily-14",
            create_rule=dlm.CfnLifecyclePolicy.CreateRuleProperty(
                interval=24, interval_unit="HOURS", times=["09:00"],
            ),
            retain_rule=dlm.CfnLifecyclePolicy.RetainRuleProperty(count=14),
            copy_tags=True,
        )
        weekly = dlm.CfnLifecyclePolicy.ScheduleProperty(
            name="weekly-8",
            create_rule=dlm.CfnLifecyclePolicy.CreateRuleProperty(
                cron_expression="cron(0 10 ? * SUN *)",
            ),
            retain_rule=dlm.CfnLifecyclePolicy.RetainRuleProperty(count=8),
            copy_tags=True,
        )

        policy = dlm.CfnLifecyclePolicy(
            self, "EbsSnapshotPolicy",
            description="Atlas box root volume — daily + weekly snapshots",
            state="ENABLED",
            execution_role_arn=dlm_role.role_arn,
            policy_details=dlm.CfnLifecyclePolicy.PolicyDetailsProperty(
                policy_type="EBS_SNAPSHOT_MANAGEMENT",
                resource_types=["VOLUME"],
                target_tags=[
                    CfnTag(key=BACKUP_TAG_KEY, value=BACKUP_TAG_VALUE),
                ],
                schedules=[daily, weekly],
            ),
        )

        CfnOutput(self, "SnapshotPolicyId", value=policy.ref)
        CfnOutput(self, "BackupTag", value=f"{BACKUP_TAG_KEY}={BACKUP_TAG_VALUE}")
