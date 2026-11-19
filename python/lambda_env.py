"""Cold-start setup shared by the API and jobs Lambdas (Phase 4, task 5).

Lambda's filesystem is read-only except ``/tmp``, and the code expects a few
things the box has by construction:

* ``HOME`` writable — matplotlib's cache, ``gspread.service_account()``'s
  ``~/.config/gspread/service_account.json``, DuckDB's extension directory.
* DuckDB's spatial extension, installed into the image at build
  (``/opt/duckdb-home``) and copied under ``HOME`` so ``INSTALL spatial`` is a
  no-op instead of a download.
* Secrets the box keeps as files or env: the Google service account and the
  Anthropic key, read from Secrets Manager by the names in
  ``ATLAS_GSPREAD_SECRET`` and ``ATLAS_ANTHROPIC_SECRET``; and, for auth, the
  CloudFront signing key and the service token (``ATLAS_CLOUDFRONT_KEY_SECRET``,
  ``ATLAS_SERVICE_TOKEN_SECRET``).

``prepare()`` is idempotent and cheap after the first call in a container.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_DUCKDB_HOME = Path('/opt/duckdb-home')
_prepared = False


def _secret(name):
    import boto3
    return boto3.client('secretsmanager').get_secret_value(SecretId=name)['SecretString']


def prepare(environ=None, secret=_secret, image_duckdb_home=IMAGE_DUCKDB_HOME):
    global _prepared
    if _prepared:
        return
    env = os.environ if environ is None else environ
    home = Path(env.setdefault('HOME', '/tmp'))
    if not os.access(home, os.W_OK):
        home = Path('/tmp')
        env['HOME'] = str(home)
    env.setdefault('MPLCONFIGDIR', str(home / '.matplotlib'))

    source = image_duckdb_home / '.duckdb'
    target = home / '.duckdb'
    if source.is_dir() and not target.exists():
        shutil.copytree(source, target)

    # A missing or unreadable secret disables the feature that needs it (sheets,
    # NL->SQL), not the whole function: it is logged and the cold start goes on.
    gspread_secret = env.get('ATLAS_GSPREAD_SECRET')
    path = home / '.config' / 'gspread' / 'service_account.json'
    if gspread_secret and not path.exists():
        value = _read(secret, gspread_secret)
        if value is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
            path.chmod(0o600)
    anthropic_secret = env.get('ATLAS_ANTHROPIC_SECRET')
    if anthropic_secret and not env.get('ANTHROPIC_API_KEY'):
        value = _read(secret, anthropic_secret)
        if value is not None:
            env['ANTHROPIC_API_KEY'] = value.strip()
    # Auth (task 6): the CloudFront signing key and the email Lambda's service
    # token. Without them, private-page cookies and email ingest stop working;
    # everything else carries on.
    for secret_env, target in (('ATLAS_CLOUDFRONT_KEY_SECRET', 'ATLAS_CLOUDFRONT_PRIVATE_KEY'),
                               ('ATLAS_SERVICE_TOKEN_SECRET', 'ATLAS_SERVICE_TOKEN')):
        name = env.get(secret_env)
        if name and not env.get(target):
            value = _read(secret, name)
            if value is not None:
                env[target] = value.strip() + ('\n' if target.endswith('PRIVATE_KEY') else '')
    _prepared = True


def _read(secret, name):
    try:
        return secret(name)
    except Exception as exc:
        logger.error(f"lambda_env: secret {name!r} unavailable, its feature is off: {exc}")
        return None
