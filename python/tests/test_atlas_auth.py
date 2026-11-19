"""Auth (Phase 4, task 6): role logic, tokens, CloudFront cookies, and enforcement.

The pure parts need PyJWT[crypto] and cryptography (in the API image); the
enforcement tests also need fastapi. Each part skips without its package.
"""
import base64
import json
import os
import sys
import time
import unittest
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import atlas_auth  # noqa: E402
from atlas_auth import ADMIN, INTERNAL, PUBLIC, SERVICE  # noqa: E402


class TestRoles(unittest.TestCase):
    def test_admin_includes_internal(self):
        self.assertEqual(atlas_auth.roles(['scvfd-admin'], 'scvfd'), {ADMIN, INTERNAL})
        self.assertEqual(atlas_auth.roles(['scvfd-internal'], 'scvfd'), {INTERNAL})

    def test_roles_are_per_atlas(self):
        self.assertEqual(atlas_auth.roles(['scvfd-admin'], 'westport'), set())

    def test_scs_admin_is_not_a_wildcard(self):
        self.assertEqual(atlas_auth.roles(['scs-admin'], 'scvfd'), set())

    def test_private_atlases_from_any_atlas_group(self):
        groups = ['westport-internal', 'south_fork_eel-admin', 'scs-admin', 'westport-admin',
                  'something-else']
        self.assertEqual(atlas_auth.private_atlases(groups), ['south_fork_eel', 'westport'])
        self.assertEqual(atlas_auth.private_atlases(None), [])


class TestAuthorize(unittest.TestCase):
    def test_public_needs_nothing(self):
        self.assertIsNone(atlas_auth.authorize(PUBLIC, 'scvfd', None))

    def test_no_session_is_login_required(self):
        self.assertEqual(atlas_auth.authorize(INTERNAL, 'scvfd', None),
                         {'error': 'login_required'})

    def test_internal_route(self):
        self.assertIsNone(atlas_auth.authorize(INTERNAL, 'scvfd', ['scvfd-internal']))
        self.assertIsNone(atlas_auth.authorize(INTERNAL, 'scvfd', ['scvfd-admin']))

    def test_admin_route_refuses_internal(self):
        self.assertEqual(atlas_auth.authorize(ADMIN, 'scvfd', ['scvfd-internal']),
                         {'error': 'forbidden', 'required': ADMIN, 'atlas': 'scvfd'})
        self.assertIsNone(atlas_auth.authorize(ADMIN, 'scvfd', ['scvfd-admin']))

    def test_other_atlas_groups_do_not_help(self):
        self.assertIsNotNone(atlas_auth.authorize(INTERNAL, 'scvfd', ['westport-admin']))

    def test_no_atlas_needs_scs_admin(self):
        self.assertIsNone(atlas_auth.authorize(ADMIN, None, ['scs-admin']))
        self.assertEqual(atlas_auth.authorize(ADMIN, None, ['scvfd-admin']),
                         {'error': 'forbidden', 'required': 'scs-admin'})

    def test_service_route(self):
        self.assertIsNone(atlas_auth.authorize(SERVICE, None, None, service_ok=True))
        self.assertIsNone(atlas_auth.authorize(SERVICE, None, ['scs-admin']))
        self.assertEqual(atlas_auth.authorize(SERVICE, None, None), {'error': 'login_required'})
        self.assertEqual(atlas_auth.authorize(SERVICE, None, ['scvfd-admin'])['error'], 'forbidden')

    def test_service_token_compare(self):
        self.assertTrue(atlas_auth.service_token_ok('abc', 'abc'))
        self.assertFalse(atlas_auth.service_token_ok('abd', 'abc'))
        self.assertFalse(atlas_auth.service_token_ok('', ''))
        self.assertFalse(atlas_auth.service_token_ok('abc', None))


class TestRouteTable(unittest.TestCase):
    def test_unlisted_is_admin(self):
        self.assertEqual(atlas_auth.required_role('/some_new_route'), ADMIN)
        self.assertEqual(atlas_auth.required_role(None), ADMIN)

    def test_mutating_routes_are_admin(self):
        for path in ('/publish', '/refresh', '/refresh_layer', '/materialize_asset',
                     '/save_config/{swalename}', '/reset-staging/{swalename}',
                     '/set-current-version/{swalename}', '/clear_layer/{swalename}/{layer_name}',
                     '/delete_layer/{swalename}/{layer_name}', '/create_atlas', '/files'):
            self.assertEqual(atlas_auth.required_role(path), ADMIN, path)

    def test_editing_routes_are_internal(self):
        for path in ('/delta_upload/{swalename}', '/store/{swalename}',
                     '/import_sheet/{swalename}/{layer_name}'):
            self.assertEqual(atlas_auth.required_role(path), INTERNAL, path)


class TestSafeNext(unittest.TestCase):
    def test_paths_pass(self):
        self.assertEqual(atlas_auth.safe_next('/scvfd/staging/outlets/console/admin/?x=1'),
                         '/scvfd/staging/outlets/console/admin/?x=1')

    def test_everything_else_is_root(self):
        for bad in (None, '', 'https://evil.example/', '//evil.example/', '/\\evil',
                    'scvfd/', '/a\r\nSet-Cookie: x'):
            self.assertEqual(atlas_auth.safe_next(bad), '/', bad)


def _rsa_key():
    pytest.importorskip('cryptography')
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cf_b64decode(value):
    return base64.b64decode(value.replace('-', '+').replace('_', '=').replace('~', '/'))


class TestCloudFrontCookies(unittest.TestCase):
    def test_policy_text(self):
        self.assertEqual(
            atlas_auth.cloudfront_policy('scvfd', 'next.fireatlas.org', 1763600000.9),
            '{"Statement":[{"Resource":"https://next.fireatlas.org/scvfd/staging/*",'
            '"Condition":{"DateLessThan":{"AWS:EpochTime":1763600000}}}]}')

    def test_b64_alphabet(self):
        self.assertEqual(atlas_auth.cloudfront_b64(b'\xfb\xff\xfe'), '-~~-')
        self.assertEqual(atlas_auth.cloudfront_b64(b'a'), 'YQ__')

    def test_signature_verifies_with_the_public_key(self):
        key = _rsa_key()
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        cookies = atlas_auth.cloudfront_cookies('westport', 'h.example', 'K123', pem, 1763600000)
        self.assertEqual(set(cookies), {'CloudFront-Policy', 'CloudFront-Signature',
                                        'CloudFront-Key-Pair-Id'})
        self.assertEqual(cookies['CloudFront-Key-Pair-Id'], 'K123')
        for value in cookies.values():
            self.assertNotRegex(value, r'[+=/]')
        policy = _cf_b64decode(cookies['CloudFront-Policy'])
        self.assertEqual(json.loads(policy)['Statement'][0]['Resource'],
                         'https://h.example/westport/staging/*')
        key.public_key().verify(_cf_b64decode(cookies['CloudFront-Signature']), policy,
                                padding.PKCS1v15(), hashes.SHA1())   # raises if wrong


ISSUER = 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST'
CLIENT = 'client123'


def _token_kit():
    """A signing key, its JWKS, and a function minting ID tokens with it."""
    jwt = pytest.importorskip('jwt')
    key = _rsa_key()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='kid1', alg='RS256', use='sig')

    def mint(**overrides):
        now = int(time.time())
        claims = {'iss': ISSUER, 'aud': CLIENT, 'token_use': 'id', 'iat': now,
                  'exp': now + 3600, 'cognito:username': 'scvfd',
                  'cognito:groups': ['scvfd-admin']}
        claims.update(overrides)
        kid = claims.pop('_kid', 'kid1')
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid': kid})
    return {'keys': [jwk]}, mint


class TestVerifyIdToken(unittest.TestCase):
    def setUp(self):
        self.jwks, self.mint = _token_kit()
        import jwt
        self.jwt = jwt

    def verify(self, token, **kw):
        return atlas_auth.verify_id_token(token, self.jwks, ISSUER, CLIENT, **kw)

    def test_valid(self):
        claims = self.verify(self.mint())
        self.assertEqual(atlas_auth.token_groups(claims), ['scvfd-admin'])

    def test_expired(self):
        with self.assertRaises(self.jwt.ExpiredSignatureError):
            self.verify(self.mint(exp=int(time.time()) - 10))
        token = self.mint()
        with self.assertRaises(self.jwt.ExpiredSignatureError):
            self.verify(token, now=time.time() + 7200)

    def test_wrong_audience_issuer_or_use(self):
        for overrides in ({'aud': 'other'}, {'iss': 'https://elsewhere'}, {'token_use': 'access'}):
            with self.assertRaises(self.jwt.InvalidTokenError, msg=overrides):
                self.verify(self.mint(**overrides))

    def test_unknown_key(self):
        with self.assertRaises(self.jwt.InvalidTokenError):
            self.verify(self.mint(_kid='other'))

    def test_another_keys_signature(self):
        _other_jwks, other_mint = _token_kit()
        with self.assertRaises(self.jwt.InvalidTokenError):
            self.verify(other_mint())


# --- Enforcement through the real app -----------------------------------------

class TestEnforcement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pytest.importorskip('fastapi')
        pytest.importorskip('jwt')
        os.environ.setdefault('SWALES_ROOT', '/fake/swales')
        from fastapi.testclient import TestClient
        import webapp
        import webapp_auth
        cls.webapp, cls.webapp_auth = webapp, webapp_auth
        cls.client = TestClient(webapp.app, base_url='https://testserver')

    def setUp(self):
        self.jwks, self.mint = _token_kit()
        env = {'ATLAS_AUTH': 'on', 'USER_POOL_ID': 'us-east-1_TEST',
               'USER_POOL_CLIENT_ID': CLIENT, 'AWS_REGION': 'us-east-1',
               'COGNITO_DOMAIN': 'auth.example', 'ATLAS_PUBLIC_HOST': 'atlas.example',
               'ATLAS_SERVICE_TOKEN': 'svc-secret'}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        jwks_patch = patch.object(self.webapp_auth, 'fetch_jwks', lambda issuer: self.jwks)
        jwks_patch.start()
        self.addCleanup(jwks_patch.stop)
        self.client.cookies.clear()

    def login_as(self, *groups):
        self.client.cookies.set(atlas_auth.SESSION_COOKIE,
                                self.mint(**{'cognito:groups': list(groups)}))

    def test_every_listed_route_exists(self):
        paths = {getattr(r, 'path', None) for r in self.webapp.app.routes}
        missing = sorted(set(atlas_auth.ROUTE_ROLES) - paths)
        self.assertEqual(missing, [], 'ROUTE_ROLES names routes the app does not have')

    def test_public_route_needs_no_login(self):
        self.assertEqual(self.client.get('/status').status_code, 200)

    def test_logged_out_admin_route_is_401_login_required(self):
        response = self.client.get('/publish', params={'swale': 'scvfd'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'login_required'})
        self.assertIn('WWW-Authenticate', response.headers)

    def test_internal_user_refused_admin_route(self):
        self.login_as('scvfd-internal')
        response = self.client.get('/publish', params={'swale': 'scvfd'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'forbidden')
        self.assertEqual(response.json()['required'], ADMIN)

    def test_atlas_from_path_param(self):
        self.login_as('westport-admin')
        response = self.client.get('/reset-staging/scvfd')
        self.assertEqual(response.json().get('error'), 'forbidden')

    def test_admin_passes_auth(self):
        self.login_as('scvfd-admin')
        from fastapi import HTTPException

        def reached_handler(*args, **kwargs):
            raise HTTPException(status_code=418)
        with patch.object(self.webapp, '_config_session', reached_handler):
            response = self.client.get('/publish', params={'swale': 'scvfd'})
        self.assertEqual(response.status_code, 418)

    def test_bad_token_is_no_session(self):
        self.client.cookies.set(atlas_auth.SESSION_COOKIE, 'not-a-jwt')
        self.assertEqual(self.client.get('/publish', params={'swale': 'scvfd'}).json(),
                         {'error': 'login_required'})

    def test_no_pool_configured_fails_closed(self):
        self.login_as('scvfd-admin')
        with patch.dict(os.environ, {'USER_POOL_ID': ''}):
            response = self.client.get('/publish', params={'swale': 'scvfd'})
        self.assertEqual(response.json(), {'error': 'login_required'})

    def test_service_token_route(self):
        response = self.client.post('/ingest/email_photo', json={})
        self.assertEqual(response.status_code, 401)
        response = self.client.post('/ingest/email_photo', json={},
                                    headers={atlas_auth.SERVICE_TOKEN_HEADER: 'svc-secret'})
        self.assertNotEqual(response.status_code, 401)   # 422: past auth, bad payload

    def test_auth_off(self):
        with patch.dict(os.environ, {'ATLAS_AUTH': 'off'}):
            response = self.client.get('/reset-staging/scvfd')
        self.assertNotEqual(response.status_code, 401)

    def test_me(self):
        self.assertEqual(self.client.get('/me').status_code, 401)
        self.login_as('scvfd-admin', 'westport-internal')
        body = self.client.get('/me').json()
        self.assertEqual(body['atlases'], {'scvfd': [ADMIN, INTERNAL], 'westport': [INTERNAL]})

    def test_login_redirects_to_cognito_with_state(self):
        response = self.client.get('/auth/login', params={'next': '//evil'},
                                   follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['location'].startswith('https://auth.example/oauth2/authorize?'))
        self.assertIn('redirect_uri=https%3A%2F%2Fatlas.example%2Fapi%2Fauth%2Fcallback',
                      response.headers['location'])
        state_cookie = response.cookies.get(self.webapp_auth.STATE_COOKIE)
        self.assertTrue(state_cookie.endswith('|/'))

    def test_callback_sets_session_and_cloudfront_cookies(self):
        from cryptography.hazmat.primitives import serialization
        pem = _rsa_key().private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode()
        token = self.mint(**{'cognito:groups': ['scvfd-admin', 'westport-internal']})
        self.client.cookies.set(self.webapp_auth.STATE_COOKIE, 'st8|/scvfd/staging/x/')
        with patch.dict(os.environ, {'ATLAS_CLOUDFRONT_PRIVATE_KEY': pem,
                                     'CLOUDFRONT_KEY_PAIR_ID': 'K1'}), \
                patch.object(self.webapp_auth, 'exchange_code', return_value={'id_token': token}):
            response = self.client.get('/auth/callback', params={'code': 'c', 'state': 'st8'},
                                       follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['location'], '/scvfd/staging/x/')
        set_cookies = response.headers.get_list('set-cookie')
        session = [c for c in set_cookies if c.startswith(atlas_auth.SESSION_COOKIE + '=')]
        self.assertEqual(len(session), 1)
        self.assertIn('Path=/api', session[0])
        self.assertIn('HttpOnly', session[0])
        self.assertIn('Secure', session[0])
        for atlas_name in ('scvfd', 'westport'):
            mine = [c for c in set_cookies if f'Path=/{atlas_name}/' in c]
            self.assertEqual(sorted(c.split('=')[0] for c in mine),
                             ['CloudFront-Key-Pair-Id', 'CloudFront-Policy',
                              'CloudFront-Signature'], atlas_name)

    def test_callback_refuses_a_mismatched_state(self):
        self.client.cookies.set(self.webapp_auth.STATE_COOKIE, 'st8|/')
        response = self.client.get('/auth/callback', params={'code': 'c', 'state': 'other'},
                                   follow_redirects=False)
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
