"""Jobs: work that outlives an API request (Phase 4, task 4).

On Lambda a FastAPI background task does not run: the container is frozen as
soon as the response goes out. So anything that answers "started" and is polled
afterwards — publish, a layer refresh, building an outlet — is a *job*: a
record written to S3, a message on a queue, and a consumer that runs it.

Two queues, one job format:

* ``lambda`` — the light-jobs queue, consumed by the API Lambda itself.
* ``box``    — the render queue, consumed by ``scripts/atlas_worker.py`` on the
  box: QGIS, slides, and slow or large inlets and eddies.

Which one is decided by ``route``, from the asset's ``runs_on`` flag (set in
the shared templates, overridable per atlas) — not from a hardcoded list.

A job's status is ``{atlas}/jobs/{id}.json`` in the private bucket, which the
console polls through ``/job-status``. Only the submitter writes ``queued``;
after that only the consumer writes it.

SQS delivers at least once, so ``run`` skips a job that already finished, and
a job found ``running`` (its consumer died) simply runs again: every job runs
in a session under the atlas lease, so a rerun never overlaps another writer.

Stdlib only; the clients are passed in.
"""
from datetime import datetime, timezone
import json
import traceback
import uuid

KINDS = ('materialize', 'refresh_layer', 'publish', 'slides',
         'add_layer', 'edit_layer', 'delete_layer')
QUEUED, RUNNING, SUCCEEDED, FAILED = 'queued', 'running', 'succeeded', 'failed'
FINISHED = (SUCCEEDED, FAILED)
TARGETS = ('lambda', 'box')

# Kept on a finished job: enough to show what went wrong, small enough to poll.
ERROR_TAIL = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def new_job(atlas: str, kind: str, requested_by: str = None, **params) -> dict:
    """A queued job. ``params`` are kind-specific: ``asset``; ``layer``,
    ``mode``, ``cascade``; ``tour``, ``gslides``."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}; expected one of {KINDS}")
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return {'id': f"{stamp}-{uuid.uuid4().hex[:8]}", 'atlas': atlas, 'kind': kind,
            'params': params, 'requested_by': requested_by,
            'state': QUEUED, 'submitted_at': _now(), 'started_at': None,
            'finished_at': None, 'attempts': 0, 'target': None,
            'result': None, 'error': None}


def status_key(atlas: str, job_id: str) -> str:
    return f"{atlas}/jobs/{job_id}.json"


def write_status(s3, bucket: str, job: dict):
    s3.put_object(Bucket=bucket, Key=status_key(job['atlas'], job['id']),
                  Body=json.dumps(job).encode(), ContentType='application/json',
                  CacheControl='no-cache')


def read_status(s3, bucket: str, atlas: str, job_id: str):
    """The job's status, or None if there is no such job."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=status_key(atlas, job_id))
    except Exception as exc:
        code = (getattr(exc, 'response', None) or {}).get('Error', {}).get('Code')
        if code in ('NoSuchKey', '404', 'NotFound'):
            return None
        raise
    return json.loads(obj['Body'].read())


# --- routing ------------------------------------------------------------------

def asset_runs_on(config: dict, asset_name: str) -> str:
    """``box`` or ``lambda`` for one asset: its ``runs_on``, resolved template first."""
    asset = (config.get('assets') or {}).get(asset_name) or {}
    value = (asset.get('config') or {}).get('runs_on') or asset.get('runs_on') or 'lambda'
    if value not in TARGETS:
        raise ValueError(f"asset {asset_name!r} has runs_on={value!r}; expected one of {TARGETS}")
    return value


def route(config: dict, job: dict, layer_inlets=None) -> str:
    """Which queue a job goes to.

    ``layer_inlets(config, layer)`` names the inlets a rebuild re-runs
    (``asset_inputs.layer_inlets``); passed in to keep this module stdlib-only.
    """
    kind, params = job['kind'], job['params']
    if kind in ('publish', 'add_layer', 'edit_layer', 'delete_layer'):
        # Layer changes re-materialize the webmaps and console, never a PDF.
        return 'lambda'
    if kind == 'slides':
        return 'box'                          # headless Chromium
    if kind == 'materialize':
        return asset_runs_on(config, params['asset'])
    if kind == 'refresh_layer':
        if params.get('cascade'):
            return 'box'                      # re-derives everything downstream
        if params.get('mode') == 'rebuild' and layer_inlets is not None:
            if any(asset_runs_on(config, inlet) == 'box'
                   for inlet in layer_inlets(config, params['layer'])):
                return 'box'
        return 'lambda'
    raise ValueError(f"unknown job kind {kind!r}")


def submit(s3, sqs, bucket: str, queue_urls: dict, job: dict, target: str) -> dict:
    """Record the job as queued, then enqueue it. Returns the job.

    Status first: a consumer that picks the message up at once must find it.
    A failed send leaves a status nobody will advance, so it is marked failed.
    """
    job = dict(job, target=target)
    write_status(s3, bucket, job)
    try:
        sqs.send_message(QueueUrl=queue_urls[target],
                         MessageBody=json.dumps({'atlas': job['atlas'], 'id': job['id']}))
    except Exception as exc:
        job = dict(job, state=FAILED, finished_at=_now(), error=f"could not enqueue: {exc}")
        write_status(s3, bucket, job)
        raise
    return job


# --- consuming ----------------------------------------------------------------

def run(s3, bucket: str, message: dict, runners: dict, retry_on=()) -> dict:
    """Run the job a queue message names; returns its final status.

    ``runners[kind](job) -> result`` does the work (it opens its own session).
    An exception from a runner is recorded as ``failed`` and *not* re-raised:
    the job ran and failed, and redelivering it would only fail again. Errors
    before the job starts (S3 unreachable) do propagate, so SQS retries them.

    ``retry_on``: exception types that mean "not now" rather than "failed" —
    a busy atlas (``atlas_lock.AtlasLocked``). The job goes back to ``queued``
    and the exception propagates, so the message is redelivered; after the
    queue's receive limit it lands in the dead-letter queue.
    """
    job = read_status(s3, bucket, message['atlas'], message['id'])
    if job is None:
        raise LookupError(f"no status for job {message['atlas']}/{message['id']}")
    if job['state'] in FINISHED:
        return job                            # a redelivery of finished work
    job = dict(job, state=RUNNING, started_at=_now(), attempts=job.get('attempts', 0) + 1,
               error=None)
    write_status(s3, bucket, job)
    try:
        runner = runners[job['kind']]
        result = runner(job)
        job = dict(job, state=SUCCEEDED, finished_at=_now(),
                   result=None if result is None else str(result)[:ERROR_TAIL])
    except retry_on as exc:
        write_status(s3, bucket, dict(job, state=QUEUED, started_at=None,
                                      error=f"will retry: {exc}"))
        raise
    except Exception as exc:
        tail = traceback.format_exc()[-ERROR_TAIL:]
        job = dict(job, state=FAILED, finished_at=_now(), error=f"{exc}\n{tail}")
    write_status(s3, bucket, job)
    return job
