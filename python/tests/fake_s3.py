"""In-memory S3 client for tests.

Faithful in the dimensions our code depends on, because a fake simpler than
production in exactly the dimension that mattered is how the Phase 3 bugs got
past the unit tests:

  * ListObjectsV2 paginates (``page_size`` defaults to S3's 1000) and omits
    ``Contents`` entirely on an empty page.
  * ETags are quoted strings, as S3 returns them.
  * Conditional PUTs fail the way S3 fails them: ``If-None-Match: *`` on an
    existing key and a mismatched ``If-Match`` raise 412 PreconditionFailed;
    ``If-Match`` on a missing key raises 404 NoSuchKey.
  * Errors carry a botocore-shaped ``response`` dict, which is all the code
    under test inspects.
"""

import hashlib
import io


class FakeClientError(Exception):
    def __init__(self, code: str, status: int, operation: str):
        super().__init__(f"An error occurred ({code}) when calling the {operation} operation")
        self.response = {
            'Error': {'Code': code, 'Message': code},
            'ResponseMetadata': {'HTTPStatusCode': status},
        }


def _bytes(body) -> bytes:
    if hasattr(body, 'read'):
        body = body.read()
    if isinstance(body, str):
        body = body.encode('utf-8')
    return bytes(body)


class FakeS3:
    def __init__(self, page_size: int = 1000):
        self.page_size = page_size
        self.objects = {}   # (bucket, key) -> {'body': bytes, 'etag': str}
        self.calls = []

    def _etag(self, body: bytes) -> str:
        return '"' + hashlib.md5(body).hexdigest() + '"'

    def put_object(self, Bucket, Key, Body=b'', IfMatch=None, IfNoneMatch=None,
                   Metadata=None, **_extra):
        self.calls.append(('put_object', Key))
        body = _bytes(Body)
        existing = self.objects.get((Bucket, Key))
        if IfNoneMatch == '*' and existing is not None:
            raise FakeClientError('PreconditionFailed', 412, 'PutObject')
        if IfMatch is not None:
            if existing is None:
                raise FakeClientError('NoSuchKey', 404, 'PutObject')
            if existing['etag'] != IfMatch:
                raise FakeClientError('PreconditionFailed', 412, 'PutObject')
        # Like S3, identical bytes get an identical ETag.
        etag = self._etag(body)
        self.objects[(Bucket, Key)] = {'body': body, 'etag': etag,
                                       'metadata': dict(Metadata or {})}
        return {'ETag': etag}

    def get_object(self, Bucket, Key):
        self.calls.append(('get_object', Key))
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise FakeClientError('NoSuchKey', 404, 'GetObject')
        return {'Body': io.BytesIO(obj['body']), 'ETag': obj['etag'],
                'ContentLength': len(obj['body']), 'Metadata': obj.get('metadata', {})}

    def head_object(self, Bucket, Key):
        self.calls.append(('head_object', Key))
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            # HEAD has no body to carry an error code, so S3's is the bare status.
            raise FakeClientError('404', 404, 'HeadObject')
        return {'ETag': obj['etag'], 'ContentLength': len(obj['body']),
                'Metadata': obj.get('metadata', {})}

    def delete_object(self, Bucket, Key):
        self.calls.append(('delete_object', Key))
        self.objects.pop((Bucket, Key), None)
        return {}

    def list_objects_v2(self, Bucket, Prefix='', ContinuationToken=None, MaxKeys=None):
        self.calls.append(('list_objects_v2', Prefix))
        keys = sorted(k for (b, k) in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken) if ContinuationToken else 0
        size = min(MaxKeys or self.page_size, self.page_size)
        page = keys[start:start + size]
        response = {'KeyCount': len(page), 'IsTruncated': start + size < len(keys)}
        if page:
            response['Contents'] = [
                {'Key': k, 'ETag': self.objects[(Bucket, k)]['etag'],
                 'Size': len(self.objects[(Bucket, k)]['body'])}
                for k in page]
        if response['IsTruncated']:
            response['NextContinuationToken'] = str(start + size)
        return response
