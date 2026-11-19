"""The API Lambda's entry point: webapp.app behind Mangum (Phase 4, task 5).

CloudFront routes ``/api/*`` to this function and forwards the path as is, while
the app's routes are unprefixed (``/job-status``, ``/delta_upload/{atlas}``), as
they were on the box. So the prefix is stripped before the app sees it.
"""
import lambda_env

lambda_env.prepare()

from mangum import Mangum  # noqa: E402
import webapp  # noqa: E402

API_PREFIX = '/api'


def strip_prefix(app, prefix=API_PREFIX):
    async def wrapped(scope, receive, send):
        if scope.get('type') in ('http', 'websocket'):
            path = scope.get('path', '')
            if path == prefix or path.startswith(prefix + '/'):
                scope = dict(scope, path=path[len(prefix):] or '/', root_path=prefix)
        await app(scope, receive, send)
    return wrapped


# lifespan off: the app has no startup/shutdown hooks that matter per request.
handler = Mangum(strip_prefix(webapp.app), lifespan="off")
