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
  ``ATLAS_GSPREAD_SECRET`` and ``ATLAS_ANTHROPIC_SECRET``.

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

    gspread_secret = env.get('ATLAS_GSPREAD_SECRET')
    if gspread_secret:
        path = home / '.config' / 'gspread' / 'service_account.json'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secret(gspread_secret))
            path.chmod(0o600)
    anthropic_secret = env.get('ATLAS_ANTHROPIC_SECRET')
    if anthropic_secret and not env.get('ANTHROPIC_API_KEY'):
        env['ANTHROPIC_API_KEY'] = secret(anthropic_secret).strip()
    _prepared = True
