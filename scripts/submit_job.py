#!/usr/bin/env python3
"""Submit one atlas job to a cloud environment's queue and wait for it,
printing its progress lines. The console's path, without a login.

    scripts/submit_job.py --env staging kennedy materialize asset=webmap
    scripts/submit_job.py --env staging kennedy refresh_layer layer=poi mode=update cascade=false
    scripts/submit_job.py --env staging kennedy publish
    scripts/submit_job.py --env staging rehearsal1 create_atlas target=box slug=rehearsal1 \\
        'name=Rehearsal 1' starter=simple \\
        'bbox={"west":-123.98,"east":-123.95,"south":40.23,"north":40.25}' \\
        default_h3_resolution=11 register_email=false

Params are key=value; a value that parses as JSON is used as JSON (numbers,
true/false, objects). ``target=lambda|box`` picks the queue (default lambda);
the API would choose it with ``atlas_jobs.route``. Written for the 2026-11-28
staging rehearsal. Needs boto3 and the ``atlas`` profile (or --profile).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
import atlas_jobs  # noqa: E402

QUEUE_NAMES = {'lambda': 'atlas-jobs-{env}', 'box': 'atlas-render-{env}'}


def value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--env', required=True, help='staging or prod')
    ap.add_argument('--profile', default='atlas')
    ap.add_argument('--region', default='us-east-1')
    ap.add_argument('--poll', type=float, default=10.0)
    ap.add_argument('atlas')
    ap.add_argument('kind', choices=atlas_jobs.KINDS)
    ap.add_argument('params', nargs='*', help='key=value')
    args = ap.parse_args()

    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3, sqs = session.client('s3'), session.client('sqs')
    bucket = f'scs-atlas-private-{args.env}'
    queues = {t: sqs.get_queue_url(QueueName=n.format(env=args.env))['QueueUrl']
              for t, n in QUEUE_NAMES.items()}

    params = {k: value(v) for k, v in (p.split('=', 1) for p in args.params)}
    target = params.pop('target', 'lambda')
    job = atlas_jobs.new_job(args.atlas, args.kind, requested_by='submit_job.py', **params)
    job = atlas_jobs.submit(s3, sqs, bucket, queues, job, target)
    print(f"submitted {args.atlas}/{job['id']} -> {target}", flush=True)

    shown = 0
    while True:
        time.sleep(args.poll)
        status = atlas_jobs.read_status(s3, bucket, args.atlas, job['id'])
        progress = status.get('progress') or []
        for stamp, message in progress[shown:]:
            print(f"  {stamp} {message}", flush=True)
        shown = len(progress)
        if status['state'] in atlas_jobs.FINISHED:
            print(status['state'], (status.get('result') or '')[:500])
            if status.get('error'):
                print(status['error'][:2000])
            sys.exit(0 if status['state'] == atlas_jobs.SUCCEEDED else 1)


if __name__ == '__main__':
    main()
