"""The API Lambda's entry point: webapp.app behind Mangum (Phase 4, task 5)."""
import lambda_env

lambda_env.prepare()

from mangum import Mangum  # noqa: E402
import webapp  # noqa: E402

# lifespan off: the app has no startup/shutdown hooks that matter per request.
handler = Mangum(webapp.app, lifespan="off")
