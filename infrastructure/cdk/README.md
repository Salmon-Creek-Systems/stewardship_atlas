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

Then set `google_client_id` in `cdk.json`. Authorized redirect URI on the Google
side is `https://scs-atlas-prod.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`.

If either value is missing the stack still deploys — it just omits the Google
provider, leaving a Cognito-only pool.

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

## Notes

- **Access model:** every resource is either public (CloudFront → S3, OAC) or
  gated (CloudFront → API Lambda, Cognito). Authorization is Cognito group
  membership — `{atlas}-admin`, `{atlas}-internal` — so onboarding is a group
  edit, not a deploy. A federated user with no groups gets nothing.
- **Buckets are RETAIN on delete**, as is the user pool. Destroying the stack
  will not take data or accounts with it.
- **Adding an atlas** to the `ATLASES` list in `app.py` creates its groups on the
  next deploy.
- **A second instance** (`-c env_name=staging`) gives a full parallel substrate
  with its own buckets and pool — this is where the real staging environment
  comes from.
