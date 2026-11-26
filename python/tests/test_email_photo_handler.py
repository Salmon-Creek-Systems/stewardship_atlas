"""The email-photo Lambda (infrastructure/lambda/email_photo_handler.py).

boto3 and requests are stubbed with patch.dict(sys.modules), so nothing leaks
into other tests and no AWS call is made.
"""
import base64
import importlib.util
import os
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

HANDLER = Path(__file__).resolve().parents[2] / 'infrastructure' / 'lambda' / 'email_photo_handler.py'

IMAGE = b'\xff\xd8 fake jpeg bytes'


def raw_email():
    msg = EmailMessage()
    msg['To'] = 'scvfd@fireatlas.org'
    msg['From'] = 'crew@example.com'
    msg['Subject'] = 'Locked gate'
    msg.set_content('layer: poi')
    msg.add_attachment(IMAGE, maintype='image', subtype='jpeg', filename='IMG_1.jpg')
    return msg.as_bytes()


class HandlerCase(unittest.TestCase):
    env = {}

    def setUp(self):
        self.s3 = MagicMock()
        self.s3.get_object.return_value = {'Body': MagicMock(read=lambda: raw_email())}
        self.secrets = MagicMock()
        self.secrets.get_secret_value.return_value = {'SecretString': 'tok-123\n'}
        boto3 = MagicMock()
        boto3.client.side_effect = lambda name: {'s3': self.s3, 'secretsmanager': self.secrets}[name]
        self.requests = MagicMock()
        self.requests.post.return_value = MagicMock(status_code=200, text='ok')

        patches = [
            patch.dict(sys.modules, {'boto3': boto3, 'requests': self.requests}),
            patch.dict(os.environ, {'WEBAPP_URL': 'https://api.test', **self.env}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for name in ('SERVICE_TOKEN_SECRET', 'IMAGE_BY_REFERENCE'):
            if name not in self.env:
                os.environ.pop(name, None)   # restored by the patch.dict above

        spec = importlib.util.spec_from_file_location('email_photo_handler_under_test', HANDLER)
        self.handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.handler)

    def run_handler(self):
        event = {'Records': [{'s3': {'bucket': {'name': 'atlas-ingress'},
                                     'object': {'key': 'incoming/abc123'}}}]}
        self.handler.handler(event, None)
        args, kwargs = self.requests.post.call_args
        return args[0], kwargs['json'], kwargs['headers']


class TestInlineWithoutToken(HandlerCase):
    """The box's form, as deployed until cutover with no secret configured."""

    def test_image_sent_inline(self):
        url, payload, _ = self.run_handler()
        self.assertEqual(url, 'https://api.test/ingest/email_photo')
        self.assertEqual(base64.b64decode(payload['image_data']), IMAGE)
        self.assertNotIn('image_key', payload)
        self.s3.put_object.assert_not_called()

    def test_no_token_header(self):
        _, _, headers = self.run_handler()
        self.assertEqual(headers, {})
        self.secrets.get_secret_value.assert_not_called()


class TestByReferenceWithToken(HandlerCase):
    env = {'SERVICE_TOKEN_SECRET': 'atlas/service-token-staging', 'IMAGE_BY_REFERENCE': '1'}

    def test_image_staged_and_sent_by_key(self):
        _, payload, _ = self.run_handler()
        self.assertEqual(payload['image_key'], 'images/abc123/IMG_1.jpg')
        self.assertNotIn('image_data', payload)
        self.s3.put_object.assert_called_once_with(
            Bucket='atlas-ingress', Key='images/abc123/IMG_1.jpg', Body=IMAGE)

    def test_staged_key_is_not_under_the_watched_prefix(self):
        # incoming/ triggers this Lambda; staging there would re-run it.
        _, payload, _ = self.run_handler()
        self.assertFalse(payload['image_key'].startswith('incoming/'))

    def test_staged_image_deleted_after_the_post(self):
        self.run_handler()
        deleted = [c.kwargs['Key'] for c in self.s3.delete_object.call_args_list]
        self.assertIn('images/abc123/IMG_1.jpg', deleted)

    def test_staged_image_deleted_when_the_post_fails(self):
        self.requests.post.side_effect = RuntimeError('timeout')
        with self.assertRaises(RuntimeError):
            self.run_handler()
        self.s3.delete_object.assert_called_once_with(
            Bucket='atlas-ingress', Key='images/abc123/IMG_1.jpg')

    def test_token_header_from_the_secret(self):
        _, _, headers = self.run_handler()
        self.assertEqual(headers, {'x-atlas-service-token': 'tok-123'})
        self.secrets.get_secret_value.assert_called_once_with(
            SecretId='atlas/service-token-staging')

    def test_token_read_once_per_container(self):
        self.run_handler()
        self.run_handler()
        self.assertEqual(self.secrets.get_secret_value.call_count, 1)


if __name__ == '__main__':
    unittest.main()
