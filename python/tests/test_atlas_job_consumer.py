"""atlas_job_consumer: the jobs Lambda and the box worker, against fakes."""
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import atlas_jobs as jobs  # noqa: E402
import atlas_job_consumer as consumer  # noqa: E402
import atlas_lock  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

BUCKET = 'privb'
QURL = 'https://sqs.us-east-1.amazonaws.com/123/atlas-render-staging'
ARN = 'arn:aws:sqs:us-east-1:123:atlas-jobs-staging'


class FakeSqs:
    def __init__(self):
        self.queue, self.deleted, self.visibility = [], [], []

    def send_message(self, QueueUrl, MessageBody):
        self.queue.append({'Body': MessageBody, 'ReceiptHandle': f"r{len(self.queue)}",
                           'MessageId': f"m{len(self.queue)}"})

    def receive_message(self, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
        return {'Messages': self.queue[:1]} if self.queue else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)
        self.queue = [m for m in self.queue if m['ReceiptHandle'] != ReceiptHandle]

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        self.visibility.append((QueueUrl, ReceiptHandle, VisibilityTimeout))


def busy(job):
    raise atlas_lock.AtlasLocked('kennedy', {'owner': 'box', 'purpose': 'refresh_layer roads'})


class ConsumerTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.sqs = FakeS3(), FakeSqs()

    def submit(self):
        job = jobs.new_job('kennedy', 'materialize', asset='runbook')
        return jobs.submit(self.s3, self.sqs, BUCKET, {'box': QURL}, job, 'box')

    def state(self, job):
        return jobs.read_status(self.s3, BUCKET, 'kennedy', job['id'])['state']


class TestBoxWorker(ConsumerTest):
    def work(self, runner, **kw):
        return consumer.work_one(self.s3, self.sqs, BUCKET, QURL, {'materialize': runner},
                                 wait_seconds=0, **kw)

    def test_idle(self):
        self.assertEqual(self.work(lambda j: None), 'idle')

    def test_a_job_runs_and_its_message_is_deleted(self):
        job = self.submit()
        self.assertEqual(self.work(lambda j: 'ok'), 'done')
        self.assertEqual(self.state(job), jobs.SUCCEEDED)
        self.assertEqual(self.sqs.queue, [])

    def test_a_failed_job_is_deleted_too(self):
        job = self.submit()

        def boom(j):
            raise ValueError('no')

        self.assertEqual(self.work(boom), 'done')
        self.assertEqual(self.state(job), jobs.FAILED)
        self.assertEqual(self.sqs.queue, [])

    def test_a_busy_atlas_comes_back_soon(self):
        job = self.submit()
        self.assertEqual(self.work(busy), 'busy')
        self.assertEqual(self.state(job), jobs.QUEUED)
        self.assertEqual(len(self.sqs.queue), 1)                      # not deleted
        self.assertEqual(self.sqs.visibility[-1][2], consumer.BUSY_RETRY_SECONDS)

    def test_a_missing_status_leaves_the_message_for_redelivery(self):
        self.sqs.send_message(QURL, json.dumps({'atlas': 'kennedy', 'id': 'gone'}))
        self.assertEqual(self.work(lambda j: 'ok'), 'error')
        self.assertEqual(len(self.sqs.queue), 1)

    def test_a_long_job_keeps_its_message_invisible(self):
        self.submit()

        def slow(j):
            time.sleep(0.25)

        self.work(slow, extend_every=0.05)
        extensions = [v for v in self.sqs.visibility if v[2] == consumer.VISIBILITY_SECONDS]
        self.assertGreaterEqual(len(extensions), 2)


class TestLambda(ConsumerTest):
    def event(self):
        return {'Records': [{'body': m['Body'], 'messageId': m['MessageId'],
                             'receiptHandle': m['ReceiptHandle'], 'eventSourceARN': ARN}
                            for m in self.sqs.queue]}

    def handle(self, runner):
        return consumer.lambda_handler(self.event(), None, clients=(self.s3, self.sqs, BUCKET),
                                       runners={'materialize': runner})

    def test_success_reports_no_failures(self):
        job = self.submit()
        self.assertEqual(self.handle(lambda j: 'ok'), {'batchItemFailures': []})
        self.assertEqual(self.state(job), jobs.SUCCEEDED)

    def test_busy_is_a_partial_failure_retried_soon(self):
        self.submit()
        self.assertEqual(self.handle(busy), {'batchItemFailures': [{'itemIdentifier': 'm0'}]})
        url, _receipt, seconds = self.sqs.visibility[-1]
        self.assertEqual(url, 'https://sqs.us-east-1.amazonaws.com/123/atlas-jobs-staging')
        self.assertEqual(seconds, consumer.BUSY_RETRY_SECONDS)

    def test_a_job_that_failed_is_not_retried(self):
        self.submit()

        def boom(j):
            raise ValueError('no')

        self.assertEqual(self.handle(boom), {'batchItemFailures': []})


if __name__ == '__main__':
    unittest.main()
