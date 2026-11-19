"""atlas_jobs: job records, status in S3, routing, and running a delivered job."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import atlas_jobs as jobs  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

BUCKET = 'privb'
QUEUES = {'lambda': 'q-light', 'box': 'q-render'}


class FakeSqs:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_message(self, QueueUrl, MessageBody):
        if self.fail:
            raise RuntimeError('sqs down')
        self.sent.append((QueueUrl, json.loads(MessageBody)))


class Busy(Exception):
    pass


CONFIG = {'assets': {
    'webmap': {'type': 'outlet', 'config': {'fetch_type': 'webmap'}},
    'runbook': {'type': 'outlet', 'config': {'fetch_type': 'runbook', 'runs_on': 'box'}},
    'cheap_runbook': {'type': 'outlet', 'runs_on': 'lambda',
                      'config': {'fetch_type': 'runbook', 'runs_on': 'lambda'}},
    'dem': {'type': 'inlet', 'config': {'runs_on': 'box', 'out_layer': 'dem'}},
    'hydrant_sheet': {'type': 'inlet', 'config': {'out_layer': 'hydrants'}},
}}


def inlets(config, layer):
    return [n for n, a in config['assets'].items()
            if a['type'] == 'inlet' and a['config'].get('out_layer') == layer]


class TestRouting(unittest.TestCase):
    def job(self, kind, **params):
        return jobs.new_job('kennedy', kind, **params)

    def test_assets_follow_runs_on(self):
        self.assertEqual(jobs.route(CONFIG, self.job('materialize', asset='webmap')), 'lambda')
        self.assertEqual(jobs.route(CONFIG, self.job('materialize', asset='runbook')), 'box')
        self.assertEqual(jobs.route(CONFIG, self.job('materialize', asset='cheap_runbook')),
                         'lambda')

    def test_publish_is_light_and_slides_are_heavy(self):
        self.assertEqual(jobs.route(CONFIG, self.job('publish')), 'lambda')
        self.assertEqual(jobs.route(CONFIG, self.job('slides', asset='slides')), 'box')

    def test_layer_changes_run_on_lambda(self):
        for kind in ('add_layer', 'edit_layer', 'delete_layer'):
            self.assertEqual(jobs.route(CONFIG, self.job(kind, layer='roads')), 'lambda')

    def test_layer_refresh(self):
        r = lambda **p: jobs.route(CONFIG, self.job('refresh_layer', **p), inlets)
        self.assertEqual(r(layer='hydrants', mode='update', cascade=False), 'lambda')
        self.assertEqual(r(layer='hydrants', mode='update', cascade=True), 'box')
        self.assertEqual(r(layer='hydrants', mode='rebuild', cascade=False), 'lambda')
        self.assertEqual(r(layer='dem', mode='rebuild', cascade=False), 'box')

    def test_bad_values_are_refused(self):
        with self.assertRaises(ValueError):
            jobs.new_job('kennedy', 'nonsense')
        bad = {'assets': {'x': {'config': {'runs_on': 'gpu'}}}}
        with self.assertRaises(ValueError):
            jobs.asset_runs_on(bad, 'x')


class TestSubmitAndRun(unittest.TestCase):
    def setUp(self):
        self.s3, self.sqs = FakeS3(), FakeSqs()

    def submit(self, **params):
        job = jobs.new_job('kennedy', 'materialize', **params)
        return jobs.submit(self.s3, self.sqs, BUCKET, QUEUES, job, 'box')

    def status(self, job):
        return jobs.read_status(self.s3, BUCKET, 'kennedy', job['id'])

    def message(self):
        return self.sqs.sent[-1][1]

    def test_submit_records_queued_then_sends(self):
        job = self.submit(asset='runbook')
        self.assertEqual(self.status(job)['state'], jobs.QUEUED)
        self.assertEqual(self.status(job)['target'], 'box')
        self.assertEqual(self.sqs.sent, [('q-render', {'atlas': 'kennedy', 'id': job['id']})])

    def test_a_failed_send_marks_the_job_failed(self):
        self.sqs.fail = True
        with self.assertRaises(RuntimeError):
            self.submit(asset='runbook')
        [key] = [k for (_b, k) in self.s3.objects]
        self.assertEqual(json.loads(self.s3.objects[(BUCKET, key)]['body'])['state'], jobs.FAILED)

    def test_success(self):
        job = self.submit(asset='runbook')
        seen = []

        def runner(j):
            seen.append(self.status(j)['state'])     # running while it works
            return 'built 3 PDFs'

        done = jobs.run(self.s3, BUCKET, self.message(), {'materialize': runner})
        self.assertEqual(seen, [jobs.RUNNING])
        self.assertEqual(done['state'], jobs.SUCCEEDED)
        self.assertEqual(self.status(job)['result'], 'built 3 PDFs')
        self.assertEqual(self.status(job)['attempts'], 1)

    def test_a_failure_is_recorded_not_raised(self):
        job = self.submit(asset='runbook')

        def runner(j):
            raise ValueError('QGIS exploded')

        done = jobs.run(self.s3, BUCKET, self.message(), {'materialize': runner})
        self.assertEqual(done['state'], jobs.FAILED)
        self.assertIn('QGIS exploded', self.status(job)['error'])

    def test_a_redelivered_finished_job_does_not_run_again(self):
        self.submit(asset='runbook')
        calls = []
        runners = {'materialize': lambda j: calls.append(1)}
        jobs.run(self.s3, BUCKET, self.message(), runners)
        jobs.run(self.s3, BUCKET, self.message(), runners)
        self.assertEqual(len(calls), 1)

    def test_a_job_left_running_by_a_dead_consumer_runs_again(self):
        job = self.submit(asset='runbook')
        jobs.write_status(self.s3, BUCKET, dict(self.status(job), state=jobs.RUNNING,
                                                attempts=1))
        done = jobs.run(self.s3, BUCKET, self.message(), {'materialize': lambda j: 'ok'})
        self.assertEqual((done['state'], done['attempts']), (jobs.SUCCEEDED, 2))

    def test_a_busy_atlas_requeues_and_raises_for_redelivery(self):
        job = self.submit(asset='runbook')

        def runner(j):
            raise Busy('lease held by refresh_layer roads')

        with self.assertRaises(Busy):
            jobs.run(self.s3, BUCKET, self.message(), {'materialize': runner}, retry_on=(Busy,))
        self.assertEqual(self.status(job)['state'], jobs.QUEUED)
        self.assertIn('will retry', self.status(job)['error'])

    def test_unknown_job_and_missing_status(self):
        self.assertIsNone(jobs.read_status(self.s3, BUCKET, 'kennedy', 'nope'))
        with self.assertRaises(LookupError):
            jobs.run(self.s3, BUCKET, {'atlas': 'kennedy', 'id': 'nope'}, {})


if __name__ == '__main__':
    unittest.main()
