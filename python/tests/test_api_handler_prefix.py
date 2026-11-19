"""api_handler.strip_prefix: CloudFront's /api/* reaches the app's unprefixed routes."""
import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_strip_prefix():
    # api_handler imports webapp and mangum at module level; only the pure
    # function is under test, so read it out of the source.
    src = (Path(__file__).parent.parent / 'api_handler.py').read_text()
    start = src.index('API_PREFIX =')
    end = src.index('# lifespan off')
    ns = {}
    exec(src[start:end], ns)
    return ns['strip_prefix']


class TestStripPrefix(unittest.TestCase):
    def seen(self, path, kind='http'):
        got = {}

        async def app(scope, receive, send):
            got.update(scope)

        asyncio.run(load_strip_prefix()(app)({'type': kind, 'path': path}, None, None))
        return got

    def test_prefixed_paths(self):
        self.assertEqual(self.seen('/api/job-status')['path'], '/job-status')
        self.assertEqual(self.seen('/api/job-status')['root_path'], '/api')
        self.assertEqual(self.seen('/api')['path'], '/')

    def test_other_paths_are_left_alone(self):
        self.assertEqual(self.seen('/apiary')['path'], '/apiary')
        self.assertEqual(self.seen('/job-status')['path'], '/job-status')
        self.assertEqual(self.seen('/api/x', kind='lifespan')['path'], '/api/x')


if __name__ == '__main__':
    unittest.main()
