"""Where /ingest/email_photo gets the image: inline base64, or a key the email
Lambda staged in the ingress bucket (email_inlet.staged_image_key_ok,
webapp._email_photo_bytes)."""
import base64
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault("SWALES_ROOT", "/fake/swales")   # webapp reads it at import

import email_inlet


class TestStagedImageKeyOk(unittest.TestCase):

    def test_a_staged_key(self):
        self.assertTrue(email_inlet.staged_image_key_ok('images/abc123/IMG_1.jpg'))

    def test_anything_else_in_the_bucket_is_refused(self):
        for key in ['incoming/abc123', 'quarantine/no-gps/abc123',
                    'images/abc123', 'images/', 'images/a/b/c.jpg',
                    'images/../incoming/abc', 'images/abc/..', 'images//x.jpg',
                    '/images/abc/x.jpg', '', None, 7]:
            self.assertFalse(email_inlet.staged_image_key_ok(key), key)


class TestEmailPhotoBytes(unittest.TestCase):

    def setUp(self):
        import webapp
        from fastapi import HTTPException
        self.load = webapp._email_photo_bytes
        self.HTTPException = HTTPException
        self.s3 = MagicMock()
        self.s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'staged')}

    def payload(self, **kw):
        return SimpleNamespace(image_data=kw.get('image_data'), image_key=kw.get('image_key'))

    def test_inline(self):
        data = base64.b64encode(b'inline').decode()
        self.assertEqual(self.load(self.payload(image_data=data), self.s3), b'inline')
        self.s3.get_object.assert_not_called()

    def test_staged_key_read_from_the_ingress_bucket(self):
        with patch.dict(os.environ, {'ATLAS_INGRESS_BUCKET': 'ingress-test'}):
            got = self.load(self.payload(image_key='images/abc/IMG_1.jpg'), self.s3)
        self.assertEqual(got, b'staged')
        self.s3.get_object.assert_called_once_with(Bucket='ingress-test',
                                                   Key='images/abc/IMG_1.jpg')

    def test_a_key_outside_images_is_refused_without_reading(self):
        with self.assertRaises(self.HTTPException) as cm:
            self.load(self.payload(image_key='incoming/abc'), self.s3)
        self.assertEqual(cm.exception.status_code, 400)
        self.s3.get_object.assert_not_called()

    def test_neither_form_is_a_400(self):
        with self.assertRaises(self.HTTPException) as cm:
            self.load(self.payload(), self.s3)
        self.assertEqual(cm.exception.status_code, 400)


if __name__ == '__main__':
    unittest.main()
