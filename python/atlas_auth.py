"""Who may do what: route roles, Cognito tokens, CloudFront cookies (Phase 4, task 6).

The model (documents/cloud_native_plan.md, 4e):

* **Data visibility has one private tier per atlas.** Everything under
  ``/{atlas}/staging/`` is served from the private bucket behind CloudFront
  signed cookies. A user in either of that atlas's groups gets its cookies.
* **Roles are capabilities, checked by the API.** Cognito groups
  ``{atlas}-internal`` and ``{atlas}-admin``; admin includes internal.
* **``scs-admin`` is admin on every atlas** (Scot, 2026-11-20), including ones
  made after the account, and is the only group allowed the few routes that
  name no atlas (``/create_atlas``, ``/files``, ``/log``...). Its CloudFront
  cookies are one set at ``Path=/`` covering ``/*/staging/*``.
* **Every route has a required role in ROUTE_ROLES; an unlisted route needs
  admin.** Fail closed: a new endpoint is admin-only until someone lists it.

Sessions last one day, which is Cognito's cap on an ID token, so the token
itself is the API session and the CloudFront cookies expire with it.

Status codes: every auth failure from the API is a 401, including "logged in
but not allowed", which is strictly a 403. The distribution maps *every* 403,
from any origin, to the login page (that is how a logged-out visitor to a
private page finds the login), so an API 403 would come back as HTML. The body
says which case it is: ``{"error": "login_required"}`` or
``{"error": "forbidden", "required": "admin"}``.

Pure: no FastAPI and no AWS calls here, so it is testable without either.
"""
import base64
import hmac
import json
import re

PUBLIC = 'public'
INTERNAL = 'internal'
ADMIN = 'admin'
SERVICE = 'service'      # the email Lambda's token (or an SCS admin)

SCS_ADMIN_GROUP = 'scs-admin'
ATLAS_ROLES = (ADMIN, INTERNAL)

SESSION_COOKIE = 'atlas_id_token'
SERVICE_TOKEN_HEADER = 'x-atlas-service-token'

# Keyed by the FastAPI route path, exactly as declared (``request.scope['route']
# .path``), so ``/delta_upload/{swalename}`` and not a concrete URL. Routes not
# listed need ADMIN. tests/test_atlas_auth.py fails on an entry that names no
# real route, so a renamed route can't silently fall back to admin unnoticed.
ROUTE_ROLES = {
    # Login itself.
    '/auth/login': PUBLIC,
    '/auth/callback': PUBLIC,
    '/auth/logout': PUBLIC,
    '/me': PUBLIC,

    # Reads and status.
    '/status': PUBLIC,
    '/job-status': PUBLIC,
    '/sql_query/{swalename}': PUBLIC,
    '/select_features/{swalename}': PUBLIC,
    '/create': PUBLIC,
    '/create-preview': PUBLIC,
    '/create-starters': PUBLIC,
    '/api/biochar/suitability': PUBLIC,
    '/api/biochar/ssurgo_point': PUBLIC,
    '/api/biochar/landscape': PUBLIC,
    '/api/biochar/goals': PUBLIC,
    '/api/biochar/biochar_db': PUBLIC,
    '/api/biochar/debug_ssurgo': PUBLIC,

    # Public contributions, as on the box.
    '/comment/{swalename}/{layer_name}': PUBLIC,
    '/ingest/web_photo': PUBLIC,

    # The email-photo Lambda, by service token.
    '/ingest/email_photo': SERVICE,

    # Editing data.
    '/delta_upload/{swalename}': INTERNAL,
    '/move_features/{swalename}': INTERNAL,
    '/store/{swalename}': INTERNAL,
    '/import_gsheet/{swalename}/{layer_name}': INTERNAL,
    '/export_gsheet/{swalename}/{layer_name}': INTERNAL,
    '/import_sheet/{swalename}/{layer_name}': INTERNAL,
    # Spends Anthropic credit.
    '/sql_generate/{swalename}': INTERNAL,

    # Everything else is ADMIN, listed or not: publish, refresh, refresh_layer,
    # materialize_asset, add/edit/delete/copy/clear layer, save_config,
    # reset-staging, set-current-version, log, files, create_atlas...
}

_RANK = {PUBLIC: 0, INTERNAL: 1, ADMIN: 2}


def required_role(route_path):
    """The role a route needs. Unlisted means ADMIN."""
    return ROUTE_ROLES.get(route_path, ADMIN)


def roles(groups, atlas):
    """The roles ``groups`` hold on ``atlas``: a subset of {'admin', 'internal'}.

    Admin includes internal. ``scs-admin`` is admin on every atlas.
    """
    groups = set(groups or ())
    if SCS_ADMIN_GROUP in groups:
        return {ADMIN, INTERNAL}
    held = set()
    if f'{atlas}-{ADMIN}' in groups:
        held |= {ADMIN, INTERNAL}
    if f'{atlas}-{INTERNAL}' in groups:
        held.add(INTERNAL)
    return held


def private_atlases(groups):
    """Atlases named by ``groups``' atlas groups (``scs-admin`` names none).

    For scs-admin, which sees every atlas, use ``cookie_scopes``.
    """
    atlases = set()
    for group in groups or ():
        if group == SCS_ADMIN_GROUP:
            continue
        atlas, sep, role = group.rpartition('-')
        if sep and atlas and role in ATLAS_ROLES:
            atlases.add(atlas)
    return sorted(atlases)


def cookie_scopes(groups):
    """The CloudFront cookie sets to issue: a list of (atlas, cookie path).

    ``atlas`` None is the all-atlases set for scs-admin, issued alone so no
    browser ever holds two cookies of the same name for one URL.
    """
    if SCS_ADMIN_GROUP in (groups or ()):
        return [(None, '/')]
    return [(atlas, f'/{atlas}/') for atlas in private_atlases(groups)]


def authorize(required, atlas, groups, service_ok=False):
    """None if allowed, else the 401 body to send.

    ``groups`` is None when there is no valid session. ``atlas`` is None for a
    route that names none, which for anything above PUBLIC means scs-admin.
    """
    if required == PUBLIC:
        return None
    if required == SERVICE:
        if service_ok or (groups is not None and SCS_ADMIN_GROUP in groups):
            return None
        if groups is None:
            return {'error': 'login_required'}
        return {'error': 'forbidden', 'required': SERVICE}
    if groups is None:
        return {'error': 'login_required'}
    if atlas is None:
        if SCS_ADMIN_GROUP in groups:
            return None
        return {'error': 'forbidden', 'required': SCS_ADMIN_GROUP}
    held = roles(groups, atlas)
    if any(_RANK[r] >= _RANK[required] for r in held):
        return None
    return {'error': 'forbidden', 'required': required, 'atlas': atlas}


def service_token_ok(presented, expected):
    """Constant-time comparison; an unset expected token never matches."""
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def safe_next(next_url):
    """A post-login redirect target, restricted to this site's own paths.

    Anything that isn't a plain absolute path (``//evil``, ``https://...``,
    ``/\\evil``) becomes '/', so the login can't be used as an open redirect.
    """
    if (not next_url or not next_url.startswith('/') or next_url.startswith('//')
            or '\\' in next_url or re.search(r'[\x00-\x1f]', next_url)):
        return '/'
    return next_url


# --- Cognito ID tokens -------------------------------------------------------

def issuer(region, user_pool_id):
    return f'https://cognito-idp.{region}.amazonaws.com/{user_pool_id}'


def verify_id_token(token, jwks, issuer_url, client_id, now=None):
    """The claims of a valid Cognito ID token; raises jwt.InvalidTokenError otherwise.

    ``jwks`` is the pool's ``/.well-known/jwks.json`` document (a dict). Checks
    the signature (RS256 only), issuer, audience, expiry, and ``token_use``.
    ``now`` is for tests.
    """
    import jwt  # PyJWT[crypto]; imported here so the module loads without it

    header = jwt.get_unverified_header(token)
    keys = {k['kid']: k for k in jwks.get('keys', [])}
    jwk = keys.get(header.get('kid'))
    if jwk is None:
        raise jwt.InvalidTokenError(f"unknown signing key {header.get('kid')!r}")
    key = jwt.PyJWK(jwk, algorithm='RS256').key
    options = {'require': ['exp', 'iat', 'iss', 'aud', 'token_use']}
    if now is not None:
        # PyJWT checks exp against the wall clock; for a fixed `now`, check here.
        options['verify_exp'] = False
    claims = jwt.decode(token, key=key, algorithms=['RS256'], audience=client_id,
                        issuer=issuer_url, options=options)
    if now is not None and claims['exp'] <= now:
        raise jwt.ExpiredSignatureError('Signature has expired')
    if claims.get('token_use') != 'id':
        raise jwt.InvalidTokenError(f"token_use is {claims.get('token_use')!r}, not 'id'")
    return claims


def token_groups(claims):
    return list(claims.get('cognito:groups') or [])


# --- CloudFront signed cookies ------------------------------------------------

def cloudfront_b64(data: bytes) -> str:
    """CloudFront's URL-safe base64: '+' -> '-', '=' -> '_', '/' -> '~'."""
    return (base64.b64encode(data).decode('ascii')
            .replace('+', '-').replace('=', '_').replace('/', '~'))


def cloudfront_policy(atlas, host, expires):
    """The custom policy for one atlas's private tier, as compact JSON.

    ``atlas`` None covers every atlas's (``/*/staging/*``; CloudFront's ``*``
    matches slashes too), for scs-admin.
    """
    statement = {
        'Resource': f"https://{host}/{atlas or '*'}/staging/*",
        'Condition': {'DateLessThan': {'AWS:EpochTime': int(expires)}},
    }
    return json.dumps({'Statement': [statement]}, separators=(',', ':'))


def cloudfront_cookies(atlas, host, key_pair_id, private_key_pem, expires):
    """The three cookie values for one atlas: {name: value}.

    Set each with the path ``cookie_scopes`` gives, so atlases' cookie sets
    don't collide (the names are fixed by CloudFront). Signature is RSA-SHA1, PKCS#1 v1.5, over the
    policy's exact bytes, as CloudFront requires.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    policy = cloudfront_policy(atlas, host, expires).encode()
    if isinstance(private_key_pem, str):
        private_key_pem = private_key_pem.encode()
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = key.sign(policy, padding.PKCS1v15(), hashes.SHA1())
    return {
        'CloudFront-Policy': cloudfront_b64(policy),
        'CloudFront-Signature': cloudfront_b64(signature),
        'CloudFront-Key-Pair-Id': key_pair_id,
    }
