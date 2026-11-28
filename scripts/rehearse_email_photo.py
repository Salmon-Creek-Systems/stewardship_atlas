#!/usr/bin/env python3
"""Rehearse the email-photo pipeline against a chosen API without SES or the
deployed Lambda: build an email around a geotagged photo, put it in the
ingress bucket under an UNWATCHED prefix (rehearsal/, not incoming/, so the
prod Lambda never fires), and run infrastructure/lambda/email_photo_handler.py
locally on it.

    scripts/rehearse_email_photo.py --env staging \\
        --api https://d1y2f6bmf8ds3m.cloudfront.net/api --atlas kennedy photo.jpeg

Sends the service token from atlas/service-token-{env} and, unless --inline,
the image by reference (staged in images/, as IMAGE_BY_REFERENCE=1 does). On
a 200 the handler deletes the test email and the staged image. The photo needs
GPS EXIF. Written for the 2026-11-28 staging rehearsal; needs boto3 and
requests (the laptop .venv has them) and the ``atlas`` profile.
"""
import argparse
import logging
import os
from email.message import EmailMessage
from pathlib import Path

HANDLER = Path(__file__).parent.parent / 'infrastructure' / 'lambda' / 'email_photo_handler.py'
INGRESS_BUCKET = 'atlas-ingress'
REHEARSAL_PREFIX = 'rehearsal/'   # NOT incoming/: that prefix triggers the deployed Lambda


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--env', required=True, help='staging or prod (which service token)')
    ap.add_argument('--api', required=True, help="the API base, e.g. https://<distribution>/api")
    ap.add_argument('--atlas', required=True)
    ap.add_argument('--body', default='', help="email body (a layer name on the first line picks the layer)")
    ap.add_argument('--inline', action='store_true', help='send the image inline (the box form)')
    ap.add_argument('--profile', default='atlas')
    ap.add_argument('photo')
    args = ap.parse_args()

    os.environ.update(AWS_PROFILE=args.profile, AWS_DEFAULT_REGION='us-east-1',
                      WEBAPP_URL=args.api.rstrip('/'),
                      SERVICE_TOKEN_SECRET=f'atlas/service-token-{args.env}',
                      IMAGE_BY_REFERENCE='0' if args.inline else '1')
    import boto3

    msg = EmailMessage()
    msg['To'] = f'{args.atlas}@fireatlas.org'
    msg['From'] = 'rehearsal@example.com'
    msg['Subject'] = 'Email-photo rehearsal'
    msg.set_content(args.body or 'rehearsal')
    photo = Path(args.photo)
    msg.add_attachment(photo.read_bytes(), maintype='image', subtype='jpeg', filename=photo.name)

    key = f'{REHEARSAL_PREFIX}{photo.stem}'
    s3 = boto3.client('s3')
    s3.put_object(Bucket=INGRESS_BUCKET, Key=key, Body=msg.as_bytes())

    # The handler uses `str | None` annotations; the laptop's Python 3.9 needs
    # them postponed to load it.
    ns = {'__name__': 'email_photo_handler'}
    exec(compile('from __future__ import annotations\n' + HANDLER.read_text(), str(HANDLER), 'exec'), ns)
    logging.basicConfig(level=logging.INFO)
    ns['handler']({'Records': [{'s3': {'bucket': {'name': INGRESS_BUCKET},
                                       'object': {'key': key}}}]}, None)

    left = s3.list_objects_v2(Bucket=INGRESS_BUCKET, Prefix=REHEARSAL_PREFIX).get('KeyCount', 0)
    staged = s3.list_objects_v2(Bucket=INGRESS_BUCKET, Prefix='images/').get('KeyCount', 0)
    print(f'left in {REHEARSAL_PREFIX}: {left}   left in images/: {staged}')


if __name__ == '__main__':
    main()
