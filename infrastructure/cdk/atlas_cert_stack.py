"""ACM certificate for the CloudFront front door.

Separate from AtlasCloudStack on purpose: DNS validation for fireatlas.org lives
in the *personal* AWS account's Route 53, so CloudFormation sits in
CREATE_IN_PROGRESS until the validation CNAME is added by hand. Keeping the cert
in its own stack means that wait doesn't block the rest of the substrate.

Order of operations:
    1. cdk deploy AtlasCert-prod --profile atlas     # blocks
    2. read the CNAME name/value from the ACM console (us-east-1) and add it to
       the fireatlas.org zone in the personal account
    3. the deploy completes; copy the CertificateArn output into cdk.json context
       as "certificate_arn", then deploy AtlasCloud-prod

CloudFront requires the certificate in us-east-1 regardless of where anything
else lives.
"""

from aws_cdk import CfnOutput, Stack, aws_certificatemanager as acm
from constructs import Construct


class AtlasCertStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        certificate = acm.Certificate(
            self, "Certificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(),
        )

        CfnOutput(self, "CertificateArn", value=certificate.certificate_arn)
        CfnOutput(self, "DomainName", value=domain_name)
