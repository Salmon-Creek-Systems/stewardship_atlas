# Cognito Identity Layer — Design Document

## Overview

Replace the current htaccess-based access control with AWS Cognito for user authentication and role-based access control. Users log in via Google SSO (leveraging existing Google accounts). The webapp Lambda generates temporary S3 credentials scoped to specific layers, allowing direct S3 file access without a persistent auth server.

---

## Core Design Goals

- No persistent authentication server
- Seamless login via Google SSO (users already logged into Google get near-instant access)
- Layer-level access control (public / internal / admin)
- Audit trail of who edited what
- Compatible with the cloud migration plan (static S3 serving stays cheap)

---

## Architecture

```
READ (protected layer):
Browser → GET /auth/credentials/{layer_name} (Lambda, one-time per session)
        → Lambda verifies Cognito token
        → Lambda calls STS → temporary scoped S3 credentials
        → Browser fetches S3 files directly using temp credentials
        (Lambda not involved in actual file transfer)

WRITE (edit):
Browser → POST /edit/{layer_name} with Cognito token → Lambda validates → processes edit

READ (public layer):
Browser → S3 directly (no auth needed)
```

---

## Components

### 1. AWS Cognito User Pool
- Manages user accounts and sessions
- Google configured as federated identity provider
- Users log in once, receive a session token
- No persistent server required — Cognito is fully managed

### 2. Google SSO Integration
- Users click "Login with Google"
- Cognito redirects to Google OAuth
- If already logged into Google (Gmail, Sheets, etc.), login is near-instant
- Google returns to Cognito with identity confirmation
- Cognito issues session token to user

### 3. Webapp Lambda — New Auth Endpoints
- `GET /auth/login` — redirect to Cognito hosted UI
- `GET /auth/callback` — handle OAuth callback, store session token
- `GET /auth/credentials/{layer_name}` — verify token, generate temporary S3 credentials
- Existing edit endpoints modified to require valid Cognito token

### 4. STS Temporary Credentials
- Lambda calls AWS STS `AssumeRole` with a scoped IAM policy
- Policy restricts access to specific S3 objects/prefixes for that layer
- Credentials are time-limited (e.g., 1 hour)
- After receiving credentials, browser fetches S3 files directly — Lambda not involved

### 5. S3 Object Tagging
- All layer objects tagged with access level: `access_level: public`, `access_level: internal`, `access_level: admin`
- IAM policies enforce tag-based access
- Tags applied automatically when layers are published to S3

---

## Layer Access Configuration

No new config format needed — uses existing `access` field in layer config:

```json
{
    "name": "hydrants",
    "access": ["public"],
    "editable_fields": ["name", "flow_rate"]
}
```

```json
{
    "name": "internal_notes",
    "access": ["internal"],
    "editable_fields": ["note"]
}
```

```json
{
    "name": "admin_data",
    "access": ["admin"],
    "editable_fields": ["status"]
}
```

---

## User Roles

| Role | Can Read | Can Edit |
|------|----------|----------|
| `viewer` | public, internal | — |
| `editor` | public, internal | public, internal |
| `admin` | public, internal, admin | public, internal, admin |

Roles stored as Cognito user attributes or in a simple DynamoDB table keyed by email.

---

## Implementation

### Credential Generation Endpoint

```python
def get_s3_credentials(layer_name: str, cognito_token: str):
    # 1. Verify Cognito token
    user = verify_cognito_token(cognito_token)
    if not user:
        return 401, "Unauthorized"

    # 2. Check layer access
    layer_config = get_layer_config(layer_name)
    required_access = layer_config.get('access', ['public'])[0]
    user_role = get_user_role(user['email'])

    if not has_permission(user_role, required_access):
        return 403, "Forbidden"

    # 3. Generate temporary scoped credentials via STS
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:GetObject"],
            "Resource": f"arn:aws:s3:::atlas-data/{atlas_name}/{layer_name}/*"
        }]
    }

    credentials = sts.assume_role(
        RoleArn="arn:aws:iam::ACCOUNT:role/AtlasLayerAccessRole",
        RoleSessionName=f"{user['email']}-{layer_name}",
        Policy=json.dumps(policy),
        DurationSeconds=3600
    )['Credentials']

    return 200, {
        "access_key": credentials['AccessKeyId'],
        "secret_key": credentials['SecretAccessKey'],
        "session_token": credentials['SessionToken'],
        "expiration": credentials['Expiration'].isoformat()
    }
```

### Frontend Flow

```javascript
// After login, store Cognito token
const token = sessionStorage.getItem('cognito_token');

// Before accessing a protected layer:
async function getLayerFile(layerName, filePath) {
    // Get temporary credentials (cached per layer per session)
    const credsResp = await fetch(`/auth/credentials/${layerName}`, {
        headers: { 'Authorization': `Bearer ${token}` }
    });
    const creds = await credsResp.json();

    // Configure AWS SDK with temp credentials
    AWS.config.update({
        accessKeyId: creds.access_key,
        secretAccessKey: creds.secret_key,
        sessionToken: creds.session_token
    });

    // Fetch file directly from S3
    const s3 = new AWS.S3();
    const url = s3.getSignedUrl('getObject', {
        Bucket: 'atlas-data',
        Key: `${atlasName}/${layerName}/${filePath}`
    });

    return fetch(url);
}
```

---

## Deployment Steps

### Phase 1: Cognito Setup
1. Create AWS Cognito User Pool
2. Configure Google as federated identity provider (requires Google OAuth credentials)
3. Create Cognito app client with redirect URIs
4. Test login flow in development

### Phase 2: Webapp Lambda Changes
1. Add Cognito JWT verification (use `python-jose` or `cognitojwt` library)
2. Add `/auth/login` and `/auth/callback` endpoints
3. Add `/auth/credentials/{layer_name}` endpoint with STS integration
4. Modify existing edit endpoints to require valid Cognito token
5. Deploy updated Lambda

### Phase 3: Frontend Changes
1. Add "Login with Google" button to edit interface
2. Store Cognito token in session storage after login
3. Wrap protected layer file fetches with credential retrieval
4. Update edit form submissions to include token in Authorization header

### Phase 4: S3 Tagging and IAM
1. Tag all existing S3 layer objects with `access_level`
2. Create IAM role `AtlasLayerAccessRole` for STS assumption
3. Attach IAM policy that enforces tag-based access
4. Test credential generation and direct S3 access

### Phase 5: Migration from htaccess
1. Keep htaccess as fallback during testing
2. Gradually disable htaccess for each layer as Cognito access is verified
3. Monitor for access issues
4. Remove htaccess entirely once stable

---

## Cost Estimate

| Component | Cost |
|-----------|------|
| Cognito | Free up to 50,000 MAUs |
| STS credential generation | ~$0 (included in Lambda calls) |
| Lambda auth endpoint calls | ~$0 at low volume |
| S3 file transfers (direct) | ~$0.023/GB |

Total additional cost for auth layer: effectively $0 at fire department scale.

---

## Open Questions

1. **User provisioning**: Do users self-register via Google, or do admins manually invite them?
2. **Role assignment**: Who assigns roles to new users? Admin UI needed?
3. **Multi-atlas permissions**: Can a user be an editor in one atlas but viewer in another?
4. **Credential refresh**: How does the frontend handle expired temporary credentials?
5. **Offline/field use**: Any requirements for access without internet connectivity?
