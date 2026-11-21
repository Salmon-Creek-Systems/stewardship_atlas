"""The API's half of auth (Phase 4, task 6): a global dependency and the login routes.

The rules are in atlas_auth (pure); this wires them into FastAPI and Cognito.

* ``enforce`` runs before every route (``FastAPI(dependencies=[...])``). It
  finds the route's required role, the atlas (``swalename`` path parameter or
  ``swale`` query parameter), and the caller's groups from the ID-token
  cookie, and raises AuthRequired (a 401) when the role is missing.
* ``/auth/login`` -> Cognito hosted UI -> ``/auth/callback`` sets the ID-token
  cookie (``Path=/api``) and one set of CloudFront cookies per atlas the user
  belongs to (``Path=/{atlas}/``; scs-admin gets one set at ``Path=/`` for
  every atlas), all expiring with the token (1 day).
* ``/auth/logout`` clears them and ends the Cognito session; ``/me`` says who
  you are.

Environment (set by the CDK stack; the secrets by lambda_env at cold start):
    USER_POOL_ID, USER_POOL_CLIENT_ID, COGNITO_DOMAIN, ATLAS_PUBLIC_HOST,
    CLOUDFRONT_KEY_PAIR_ID, ATLAS_CLOUDFRONT_PRIVATE_KEY, ATLAS_SERVICE_TOKEN,
    ATLAS_ORIGIN_TOKEN(_SECRET), AWS_REGION. ``ATLAS_AUTH=off`` disables role
    enforcement: tests only.

The function URL is open (auth NONE; see the CDK stack on why not OAC), so
``enforce`` first requires the origin token CloudFront adds to every request.
It is required whenever ``ATLAS_ORIGIN_TOKEN_SECRET`` is set, which only the
cloud stack does; if the secret couldn't be read, every request is refused.

A missing pool id fails closed: nobody has a session, so only public routes
(and the service token) work.
"""
import base64
import logging
import os
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

import atlas_auth
import config_checks

logger = logging.getLogger(__name__)

router = APIRouter()

ORIGIN_TOKEN_HEADER = 'x-atlas-origin-token'
STATE_COOKIE = 'atlas_auth_state'
STATE_TTL = 600
API_PATH = '/api'


class AuthRequired(Exception):
    def __init__(self, body):
        super().__init__(body.get('error'))
        self.body = body


def auth_required_response(exc: AuthRequired):
    """401 for both "log in" and "not allowed"; see atlas_auth on why not 403."""
    return JSONResponse(status_code=401, content=exc.body,
                        headers={'WWW-Authenticate': 'Cookie realm="atlas"'})


def _env(name, default=None):
    return os.environ.get(name, default)


def enabled():
    return _env('ATLAS_AUTH', 'on').lower() != 'off'


# --- Session -----------------------------------------------------------------

_jwks_cache = {}


def fetch_jwks(issuer_url):
    """The pool's signing keys, fetched once per container."""
    if issuer_url not in _jwks_cache:
        import requests
        response = requests.get(f'{issuer_url}/.well-known/jwks.json', timeout=5)
        response.raise_for_status()
        _jwks_cache[issuer_url] = response.json()
    return _jwks_cache[issuer_url]


def _issuer():
    pool = _env('USER_POOL_ID')
    if not pool:
        return None
    return atlas_auth.issuer(_env('AWS_REGION', 'us-east-1'), pool)


def verify(token):
    """Claims for a valid ID token, else None. Never raises."""
    issuer_url = _issuer()
    if not token or not issuer_url:
        return None
    try:
        return atlas_auth.verify_id_token(token, fetch_jwks(issuer_url), issuer_url,
                                          _env('USER_POOL_CLIENT_ID'))
    except Exception as exc:
        logger.info(f"auth: rejected session token: {exc}")
        return None


def session_claims(request: Request):
    return verify(request.cookies.get(atlas_auth.SESSION_COOKIE))


def request_atlas(request: Request):
    return request.path_params.get('swalename') or request.query_params.get('swale')


def check_origin(request: Request):
    """Raise AuthRequired unless the request came through our CloudFront."""
    if not _env('ATLAS_ORIGIN_TOKEN_SECRET'):
        return
    if not atlas_auth.service_token_ok(request.headers.get(ORIGIN_TOKEN_HEADER),
                                       _env('ATLAS_ORIGIN_TOKEN')):
        raise AuthRequired({'error': 'not_via_cloudfront'})


async def enforce(request: Request):
    """The global dependency: raise AuthRequired unless the caller may use this route."""
    check_origin(request)
    if not enabled():
        return
    route = request.scope.get('route')
    required = atlas_auth.required_role(getattr(route, 'path', None))
    if required == atlas_auth.PUBLIC:
        return
    claims = session_claims(request)
    groups = atlas_auth.token_groups(claims) if claims else None
    service_ok = atlas_auth.service_token_ok(
        request.headers.get(atlas_auth.SERVICE_TOKEN_HEADER), _env('ATLAS_SERVICE_TOKEN'))
    refusal = atlas_auth.authorize(required, request_atlas(request), groups, service_ok)
    if refusal is not None:
        raise AuthRequired(refusal)


# --- Login routes ------------------------------------------------------------

def _callback_url():
    return f"https://{_env('ATLAS_PUBLIC_HOST')}{API_PATH}/auth/callback"


def exchange_code(code):
    """Trade an authorization code for tokens at the Cognito token endpoint."""
    import requests
    response = requests.post(
        f"https://{_env('COGNITO_DOMAIN')}/oauth2/token",
        data={'grant_type': 'authorization_code', 'client_id': _env('USER_POOL_CLIENT_ID'),
              'code': code, 'redirect_uri': _callback_url()},
        headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=10)
    response.raise_for_status()
    return response.json()


def _cookie(response, name, value, path, max_age):
    response.set_cookie(name, value, max_age=max_age, path=path, secure=True,
                        httponly=True, samesite='lax')


def _state_value(state, next_url):
    """``{state}.{base64url(next)}``: cookie-safe, so it is never quoted."""
    encoded = base64.urlsafe_b64encode(next_url.encode()).decode().rstrip('=')
    return f'{state}.{encoded}'


def _parse_state(value):
    state, _, encoded = (value or '').partition('.')
    try:
        next_url = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode()
    except Exception:
        next_url = '/'
    return state, next_url


@router.get('/auth/login')
async def auth_login(next: str = '/'):
    state = secrets.token_urlsafe(16)
    query = urlencode({'response_type': 'code', 'client_id': _env('USER_POOL_CLIENT_ID'),
                       'redirect_uri': _callback_url(), 'scope': 'openid email profile',
                       'state': state})
    response = RedirectResponse(f"https://{_env('COGNITO_DOMAIN')}/oauth2/authorize?{query}",
                                status_code=302)
    _cookie(response, STATE_COOKIE, _state_value(state, atlas_auth.safe_next(next)),
            f'{API_PATH}/auth', STATE_TTL)
    return response


@router.get('/auth/callback')
async def auth_callback(request: Request, code: str = None, state: str = None):
    expected, next_url = _parse_state(request.cookies.get(STATE_COOKIE))
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        return JSONResponse(status_code=400, content={
            'error': 'bad_login_state', 'detail': 'Login expired or was started elsewhere; try again.'})
    try:
        tokens = exchange_code(code)
    except Exception as exc:
        logger.error(f"auth: code exchange failed: {exc}")
        return JSONResponse(status_code=502, content={'error': 'login_failed'})
    id_token = tokens.get('id_token')
    claims = verify(id_token)
    if claims is None:
        return JSONResponse(status_code=502, content={'error': 'login_failed'})

    max_age = max(0, int(claims['exp'] - time.time()))
    response = RedirectResponse(atlas_auth.safe_next(next_url), status_code=302)
    _cookie(response, atlas_auth.SESSION_COOKIE, id_token, API_PATH, max_age)
    response.delete_cookie(STATE_COOKIE, path=f'{API_PATH}/auth')

    private_key = _env('ATLAS_CLOUDFRONT_PRIVATE_KEY')
    key_pair_id = _env('CLOUDFRONT_KEY_PAIR_ID')
    scopes = atlas_auth.cookie_scopes(atlas_auth.token_groups(claims))
    if scopes and not (private_key and key_pair_id):
        logger.error("auth: no CloudFront signing key; private pages will 403")
    elif scopes:
        for atlas_name, path in scopes:
            cookies = atlas_auth.cloudfront_cookies(
                atlas_name, _env('ATLAS_PUBLIC_HOST'), key_pair_id, private_key, claims['exp'])
            for name, value in cookies.items():
                _cookie(response, name, value, path, max_age)
    logger.info(f"auth: login {claims.get('cognito:username')} -> {[p for _, p in scopes]}")
    return response


@router.get('/auth/logout')
async def auth_logout(request: Request):
    claims = session_claims(request)
    atlases = set(config_checks.ACTIVE_ATLASES)
    if claims:
        atlases |= set(atlas_auth.private_atlases(atlas_auth.token_groups(claims)))
    query = urlencode({'client_id': _env('USER_POOL_CLIENT_ID'),
                       'logout_uri': f"https://{_env('ATLAS_PUBLIC_HOST')}/"})
    response = RedirectResponse(f"https://{_env('COGNITO_DOMAIN')}/logout?{query}",
                                status_code=302)
    response.delete_cookie(atlas_auth.SESSION_COOKIE, path=API_PATH)
    paths = ['/'] + [f'/{atlas_name}/' for atlas_name in sorted(atlases)]
    for path in paths:
        for name in ('CloudFront-Policy', 'CloudFront-Signature', 'CloudFront-Key-Pair-Id'):
            response.delete_cookie(name, path=path)
    return response


@router.get('/me')
async def me(request: Request):
    claims = session_claims(request)
    if claims is None:
        return auth_required_response(AuthRequired({'error': 'login_required'}))
    groups = atlas_auth.token_groups(claims)
    return {
        'username': claims.get('cognito:username'),
        'email': claims.get('email'),
        'groups': groups,
        'expires': claims['exp'],
        'all_atlases': atlas_auth.SCS_ADMIN_GROUP in groups,
        'atlases': {a: sorted(atlas_auth.roles(groups, a))
                    for a in atlas_auth.private_atlases(groups)},
    }
