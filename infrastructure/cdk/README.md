# CDK infrastructure

All AWS infrastructure for Stewardship Atlas. See `documents/cloud_native_plan.md`
for the migration this is part of.

## Stacks

| Stack | What it is |
|---|---|
| `AtlasCloud-prod` | Cloud-native substrate: buckets, CloudFront, Cognito, SQS, API Lambda + ECR (Phase 1a) |
| `AtlasCert-prod` | ACM certificate for the CloudFront domain (separate because DNS validation is manual) |
| `AtlasGithubOidc` | GitHub Actions OIDC provider + deploy role (Phase 1b) |
| `AtlasEmailPhotoStack` | SES → S3 → Lambda email photo inlet (pre-existing) |
| `KennedyDns`, `KennedySecurityGroup` | Per-atlas DNS/SG provisioning (pre-existing) |

Everything cloud-native lives in **us-east-1** — CloudFront requires its
certificate there, and `scs-atlas-data` and the email stack are already there.

## Toolchain

Neither `node` nor `cdk` is on the system by default. Install Node from the
official tarball (no Homebrew):

```bash
curl -fsSLO https://nodejs.org/dist/v24.19.0/node-v24.19.0-darwin-arm64.tar.xz
tar -xJf node-v24.19.0-darwin-arm64.tar.xz -C ~/.local
mv ~/.local/node-v24.19.0-darwin-arm64 ~/.local/node
export PATH="$HOME/.local/node/bin:$PATH"   # add to ~/.zshrc
npm install -g aws-cdk
```

Python side (the venv path is what `cdk.json` invokes):

```bash
cd /path/to/stewardship_atlas
python3 -m venv .venv
.venv/bin/pip install -r infrastructure/cdk/requirements.txt
```

`aws-cdk-lib` is pinned to 2.255.0 — the last release supporting Python 3.9,
which is all macOS CommandLineTools ships. Installing a modern Python locally
would let us drop the pin.

Synth requires the (gitignored) email-inlet bundle to exist, because `app.py`
constructs every stack:

```bash
pip3 install -r infrastructure/lambda/requirements.txt -t infrastructure/lambda_build/
cp infrastructure/lambda/email_photo_handler.py infrastructure/lambda_build/
```

## First deploy

Order matters — steps 1 and 2 involve manual, out-of-band work.

**1. Google OAuth client secret.** Cognito's Google IdP needs a client ID and
secret from a Google Cloud OAuth client. The ID goes in `cdk.json` context; the
secret goes in Secrets Manager, never in the repo:

```bash
aws secretsmanager create-secret \
  --name atlas/google-oauth-client-secret \
  --secret-string 'THE_SECRET' \
  --region us-east-1 --profile atlas
```

Then set `google_client_id` in `cdk.json`. Authorized redirect URIs on the
Google side are `https://scs-atlas-auth-{prod,staging}.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`
(the `scs-atlas-auth-` domains belong to the pools created 2026-11-19; the old
`scs-atlas-{env}` ones went with the retired pools).

If either value is missing the stack still deploys — it just omits the Google
provider, leaving a Cognito-only pool.

**1b. Auth secrets (task 6), per environment.** The CloudFront signing key,
the email Lambda's service token, and the origin token CloudFront sends to the
API's open function URL (the API refuses requests without it). The private key goes straight to Secrets
Manager and is deleted locally; the public half is committed as
`keys/cloudfront-{env}.pem` and becomes the distribution's key group:

```bash
umask 077; ENV=staging
openssl genrsa -out /tmp/cf.key 2048
openssl rsa -in /tmp/cf.key -pubout -out keys/cloudfront-$ENV.pem
aws secretsmanager create-secret --name atlas/cloudfront-signing-key-$ENV \
  --secret-string file:///tmp/cf.key --region us-east-1 --profile atlas
rm /tmp/cf.key
openssl rand -hex 32 | tr -d '\n' > /tmp/st
aws secretsmanager create-secret --name atlas/service-token-$ENV \
  --secret-string file:///tmp/st --region us-east-1 --profile atlas
rm /tmp/st
openssl rand -hex 32 | tr -d '\n' > /tmp/ot
aws secretsmanager create-secret --name atlas/origin-token-$ENV \
  --secret-string file:///tmp/ot --region us-east-1 --profile atlas
rm /tmp/ot
```

Rotating the key: replace both halves together and deploy; existing logins
fail at CloudFront until their next login.

**2. Certificate.** `fireatlas.org`'s Route 53 zone is in the *personal* AWS
account, so validation is a manual record:

```bash
cdk deploy AtlasCert-prod --profile atlas    # blocks on validation
```

Copy the CNAME name/value from the ACM console (us-east-1) into the
`fireatlas.org` zone in the personal account. When the deploy finishes, put the
`CertificateArn` output into `cdk.json` as `certificate_arn`. Without it the
distribution comes up on its `*.cloudfront.net` name, which is fine for
validation work.

**3. Substrate.** The first deploy has to come from a laptop, because it creates
the ECR repository that CI pushes to, and no Docker is available locally:

```bash
cdk deploy AtlasCloud-prod --profile atlas
```

With no `api_image_tag`, the API Lambda deploys as an inline placeholder. Once CI
has pushed an image, deploys pass `-c api_image_tag=<sha>` and the function
becomes a container image. Flipping between the two replaces the function (and
its function URL), so don't do it casually.

**4. CI deploy role.**

```bash
cdk deploy AtlasGithubOidc --profile atlas
```

After this, `.github/workflows/deploy-cloud.yml` can deploy on push to `main` or
by manual dispatch. No AWS keys are stored in GitHub.

## Verifying

```bash
aws cloudformation describe-stacks --stack-name AtlasCloud-prod \
  --region us-east-1 --profile atlas --query 'Stacks[0].Outputs'

curl https://<DistributionDomain>/api/health     # placeholder JSON
```

The API Lambda has no fixed name — a custom name would block the zip→container
replacement — so find it via the `ApiFunctionName` output rather than guessing.

## Notes

- **Access model (task 6):** public outlets come from the outlets bucket with no
  login. `/{atlas}/staging/*` comes from the private bucket for holders of that
  atlas's CloudFront signed cookies, which `/api/auth/callback` sets for every
  atlas in the user's groups. The API checks the Cognito ID-token cookie against
  a role per route (`python/atlas_auth.py`). Groups: `{atlas}-admin`,
  `{atlas}-internal` and `scs-admin` (admin on every atlas, so an SCS account
  needs only that one); onboarding is a group edit, not a deploy.
  A federated user with no groups gets nothing.
- **Every 403 is the login page.** The distribution answers any 403 with
  `/local/auth/login-required.html` (published by `scripts/publish_site_assets.py`),
  which is how a logged-out visitor to a private page finds the login. So both
  buckets grant CloudFront `ListBucket` (a missing key is a 404) and the API
  never returns 403: its auth failures are 401.
- **Accounts** are created by hand, no email needed:
  ```bash
  POOL=$(aws cloudformation describe-stacks --stack-name AtlasCloud-staging --region us-east-1 \
    --profile atlas --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
  aws cognito-idp admin-create-user --user-pool-id $POOL --username scvfd \
    --message-action SUPPRESS --region us-east-1 --profile atlas
  aws cognito-idp admin-set-user-password --user-pool-id $POOL --username scvfd \
    --password '…' --permanent --region us-east-1 --profile atlas
  aws cognito-idp admin-add-user-to-group --user-pool-id $POOL --username scvfd \
    --group-name scvfd-admin --region us-east-1 --profile atlas
  ```
- **Buckets are RETAIN on delete**, as is the user pool. Destroying the stack
  will not take data or accounts with it.
- **Adding an atlas** to the `ATLASES` list in `app.py` creates its groups on the
  next deploy.
- **A second instance** (`-c env_name=staging`) gives a full parallel substrate
  with its own buckets and pool — this is where the real staging environment
  comes from.
