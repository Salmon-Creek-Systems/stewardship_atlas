"""The two job consumers: the jobs Lambda and the box worker.

Both take a queue message ``{atlas, id}`` and hand it to ``atlas_jobs.run``
with the runners in ``atlas_job_runners``. They differ only in how SQS is
driven:

* ``lambda_handler`` — an SQS event source (batch size 1, partial batch
  responses). A busy atlas is returned to the queue after ``BUSY_RETRY_SECONDS``
  rather than after the full visibility timeout.
* ``work_one`` / ``serve`` — the box: long-poll, one job at a time (the box
  has run out of memory running jobs side by side), and keep extending the
  message's visibility while a long job runs, so SQS does not hand it to
  anyone else mid-render.

Environment: ``ATLAS_PRIVATE_BUCKET`` (via ``atlas_store.cloud_settings``),
``ATLAS_JOBS_REGION`` (the queues' region; default us-east-1).
"""
import json
import logging
import os
import threading

import atlas_jobs
import atlas_lock

logger = logging.getLogger(__name__)

BUSY_RETRY_SECONDS = 60
VISIBILITY_SECONDS = 900          # what each extension grants
EXTEND_EVERY_SECONDS = 300


def _clients():
    import boto3
    import atlas_store
    region = os.environ.get('ATLAS_JOBS_REGION', 'us-east-1')
    return (atlas_store._s3(), boto3.client('sqs', region_name=region),
            atlas_store.cloud_settings({})['private_bucket'])


def _runners():
    import atlas_job_runners
    return atlas_job_runners.RUNNERS


def handle(s3, bucket, body: str, runners) -> str:
    """Run one message body. Returns ``done`` or ``busy``; other errors raise."""
    try:
        job = atlas_jobs.run(s3, bucket, json.loads(body), runners,
                             retry_on=(atlas_lock.AtlasLocked,))
    except atlas_lock.AtlasLocked as exc:
        logger.info(f"job {body}: atlas busy, will retry: {exc}")
        return 'busy'
    logger.info(f"job {job['atlas']}/{job['id']} {job['kind']}: {job['state']}")
    return 'done'


# --- Lambda ---------------------------------------------------------------------

def lambda_handler(event, context, clients=None, runners=None):
    s3, sqs, bucket = clients or _clients()
    runners = runners or _runners()
    failures = []
    for record in event.get('Records', []):
        try:
            outcome = handle(s3, bucket, record['body'], runners)
        except Exception:
            logger.exception(f"job {record['body']}: could not run")
            outcome = 'error'
        if outcome == 'busy':
            queue_url = _queue_url_from_arn(record['eventSourceARN'])
            sqs.change_message_visibility(QueueUrl=queue_url,
                                          ReceiptHandle=record['receiptHandle'],
                                          VisibilityTimeout=BUSY_RETRY_SECONDS)
        if outcome != 'done':
            failures.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failures}


def _queue_url_from_arn(arn: str) -> str:
    # arn:aws:sqs:{region}:{account}:{name}
    _, _, _, region, account, name = arn.split(':', 5)
    return f"https://sqs.{region}.amazonaws.com/{account}/{name}"


# --- box ------------------------------------------------------------------------

class VisibilityExtender:
    """Keeps a received message invisible while its job runs."""

    def __init__(self, sqs, queue_url, receipt, every=EXTEND_EVERY_SECONDS):
        self._sqs, self._url, self._receipt, self._every = sqs, queue_url, receipt, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name='job-visibility')

    def _run(self):
        while not self._stop.wait(self._every):
            try:
                self._sqs.change_message_visibility(QueueUrl=self._url, ReceiptHandle=self._receipt,
                                                    VisibilityTimeout=VISIBILITY_SECONDS)
            except Exception as exc:
                logger.warning(f"could not extend job visibility, will retry: {exc}")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def work_one(s3, sqs, bucket, queue_url, runners, wait_seconds=20, extend_every=None) -> str:
    """Receive and run at most one message. Returns ``idle``, ``done``, ``busy`` or ``error``."""
    got = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1,
                              WaitTimeSeconds=wait_seconds)
    messages = got.get('Messages') or []
    if not messages:
        return 'idle'
    msg = messages[0]
    try:
        with VisibilityExtender(sqs, queue_url, msg['ReceiptHandle'],
                                extend_every or EXTEND_EVERY_SECONDS):
            outcome = handle(s3, bucket, msg['Body'], runners)
    except Exception:
        # Not started (S3 unreachable, no status): leave the message to come
        # back after its visibility timeout, and toward the dead-letter queue.
        logger.exception(f"job {msg['Body']}: could not run")
        return 'error'
    if outcome == 'busy':
        sqs.change_message_visibility(QueueUrl=queue_url, ReceiptHandle=msg['ReceiptHandle'],
                                      VisibilityTimeout=BUSY_RETRY_SECONDS)
        return 'busy'
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg['ReceiptHandle'])
    return 'done'


def serve(queue_url, stop=None):
    """The box worker loop; ``stop`` is an optional ``threading.Event``."""
    s3, sqs, bucket = _clients()
    runners = _runners()
    logger.info(f"atlas worker: consuming {queue_url}")
    while stop is None or not stop.is_set():
        work_one(s3, sqs, bucket, queue_url, runners)
